import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from relay.domain.delivery_attempts import AttemptErrorClass

RESPONSE_SNIPPET_MAX_LEN = 2048
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_READ_TIMEOUT_SECONDS = 10.0


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
    network traffic. Phase 3 adds the SSRF-resistant, DNS-pinned adapter; this port doesn't
    change shape when that lands.
    """

    async def send(
        self, *, url: str, payload: bytes, headers: dict[str, str]
    ) -> OutboundHttpResult: ...


class HttpxOutboundSender:
    """Real adapter: a persistent httpx.AsyncClient with explicit connect/read timeouts.
    Classifies timeouts and connection failures into AttemptErrorClass here so
    DeliveryAttemptService never has to know about httpx's exception hierarchy.
    """

    def __init__(
        self,
        *,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        read_timeout: float = DEFAULT_READ_TIMEOUT_SECONDS,
    ) -> None:
        timeout = httpx.Timeout(
            connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout
        )
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send(
        self, *, url: str, payload: bytes, headers: dict[str, str]
    ) -> OutboundHttpResult:
        start = time.monotonic()
        try:
            response = await self._client.post(url, content=payload, headers=headers)
        except httpx.TimeoutException:
            return OutboundHttpResult(
                latency_ms=_elapsed_ms(start), error_class=AttemptErrorClass.TIMEOUT
            )
        except httpx.HTTPError:
            return OutboundHttpResult(
                latency_ms=_elapsed_ms(start), error_class=AttemptErrorClass.CONNECTION_ERROR
            )

        latency_ms = _elapsed_ms(start)
        snippet = response.text[:RESPONSE_SNIPPET_MAX_LEN]
        if 200 <= response.status_code < 300:
            return OutboundHttpResult(
                latency_ms=latency_ms, status_code=response.status_code, response_snippet=snippet
            )
        return OutboundHttpResult(
            latency_ms=latency_ms,
            status_code=response.status_code,
            error_class=AttemptErrorClass.HTTP_ERROR,
            response_snippet=snippet,
        )


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
