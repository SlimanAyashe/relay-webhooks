import asyncio
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin

import httpx

from relay.domain.delivery_attempts import AttemptErrorClass
from relay.infra.pinned_transport import PinnedAsyncTransport
from relay.infra.settings import get_settings
from relay.infra.ssrf_guard import Resolver, check_url, default_resolver

RESPONSE_SNIPPET_MAX_LEN = 2048

# How many redirect hops to follow, each re-validated by the SSRF guard before connecting.
# httpx.AsyncClient defaults to follow_redirects=False, and this adapter also passes it
# explicitly per-request -- redirects are always handled by this loop, never by httpx.
MAX_REDIRECT_HOPS = 3


@dataclass(frozen=True, slots=True)
class OutboundHttpResult:
    latency_ms: int
    status_code: int | None = None
    error_class: AttemptErrorClass | None = None
    response_snippet: str | None = None

    @property
    def succeeded(self) -> bool:
        return (
            self.error_class is None
            and self.status_code is not None
            and 200 <= self.status_code < 300
        )


class OutboundHttpSender(Protocol):
    """Port for sending one outbound delivery attempt. DeliveryAttemptService depends on
    this, not on httpx directly, so tests can substitute a fake adapter without any real
    network traffic. The real adapter (HttpxOutboundSender) is SSRF-guarded and IP-pinned;
    that's entirely internal to the adapter, so this port's shape never had to change for it.
    """

    async def send(
        self, *, url: str, payload: bytes, headers: dict[str, str]
    ) -> OutboundHttpResult: ...


class HttpxOutboundSender:
    """Real adapter. Every send() call -- including retries and redirect hops -- runs the
    SSRF guard first and connects only to the address it validated, via a fresh
    IP-pinned transport per hop (see relay.infra.pinned_transport). A blocked destination
    never reaches httpx at all; it's classified as AttemptErrorClass.SSRF_BLOCKED and
    returned like any other non-2xx outcome, so DeliveryAttemptService doesn't need to know
    SSRF is even a concept -- it just sees a failed attempt with a specific error class.

    No persistent httpx.AsyncClient is kept across calls (Phase 2's adapter had one): the
    transport differs per destination IP, so each send() opens a short-lived client scoped
    to that one request (and its redirect hops).
    """

    def __init__(
        self,
        *,
        connect_timeout: float | None = None,
        read_timeout: float | None = None,
        allowed_ports: frozenset[int] | None = None,
        resolver: Resolver = default_resolver,
        user_agent: str | None = None,
    ) -> None:
        settings = get_settings()
        connect_timeout = (
            connect_timeout
            if connect_timeout is not None
            else settings.outbound_connect_timeout_seconds
        )
        read_timeout = (
            read_timeout if read_timeout is not None else settings.outbound_read_timeout_seconds
        )
        self._timeout = httpx.Timeout(
            connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout
        )
        self._allowed_ports = (
            allowed_ports if allowed_ports is not None else settings.ssrf_allowed_ports
        )
        self._resolver = resolver
        self._user_agent = user_agent if user_agent is not None else settings.outbound_user_agent

    async def aclose(self) -> None:
        """No persistent client to close -- kept as a no-op so worker shutdown code
        (`await owned_sender.aclose()`) doesn't need to know this adapter changed shape.
        """

    async def send(
        self, *, url: str, payload: bytes, headers: dict[str, str]
    ) -> OutboundHttpResult:
        start = time.monotonic()
        current_url = url
        # A fixed, identifying User-Agent on every outbound delivery request (including
        # every redirect hop) so an abuse report against a customer's server is traceable
        # back to this service and answerable. Set first so a caller-supplied header
        # (there isn't one today) could still override it.
        headers = {"User-Agent": self._user_agent, **headers}

        for hop in range(MAX_REDIRECT_HOPS + 1):
            check = await asyncio.to_thread(
                check_url, current_url, allowed_ports=self._allowed_ports, resolver=self._resolver
            )
            if not check.allowed:
                return OutboundHttpResult(
                    latency_ms=_elapsed_ms(start),
                    error_class=AttemptErrorClass.SSRF_BLOCKED,
                    response_snippet=check.reason,
                )
            assert check.hostname is not None and check.resolved_ip is not None

            transport = PinnedAsyncTransport(
                pinned_ip=check.resolved_ip, original_host=check.hostname
            )
            try:
                async with httpx.AsyncClient(transport=transport, timeout=self._timeout) as client:
                    response = await client.post(
                        current_url, content=payload, headers=headers, follow_redirects=False
                    )
            except httpx.TimeoutException:
                return OutboundHttpResult(
                    latency_ms=_elapsed_ms(start), error_class=AttemptErrorClass.TIMEOUT
                )
            except httpx.HTTPError:
                return OutboundHttpResult(
                    latency_ms=_elapsed_ms(start), error_class=AttemptErrorClass.CONNECTION_ERROR
                )

            location = response.headers.get("location")
            if 300 <= response.status_code < 400 and location:
                if hop == MAX_REDIRECT_HOPS:
                    return OutboundHttpResult(
                        latency_ms=_elapsed_ms(start),
                        status_code=response.status_code,
                        error_class=AttemptErrorClass.HTTP_ERROR,
                        response_snippet=f"redirect budget exceeded at {location!r}",
                    )
                # Re-validated on the very next loop iteration -- a redirect to a forbidden
                # IP is blocked before any connection to it is ever attempted.
                current_url = urljoin(current_url, location)
                continue

            latency_ms = _elapsed_ms(start)
            snippet = response.text[:RESPONSE_SNIPPET_MAX_LEN]
            if 200 <= response.status_code < 300:
                return OutboundHttpResult(
                    latency_ms=latency_ms,
                    status_code=response.status_code,
                    response_snippet=snippet,
                )
            return OutboundHttpResult(
                latency_ms=latency_ms,
                status_code=response.status_code,
                error_class=AttemptErrorClass.HTTP_ERROR,
                response_snippet=snippet,
            )

        # Unreachable: the loop above always returns by hop == MAX_REDIRECT_HOPS.
        raise AssertionError("unreachable")  # pragma: no cover


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
