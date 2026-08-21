"""SSRF-guard + IP-pinned-transport behavior of the real HttpxOutboundSender adapter,
exercised via respx-mocked destinations (never real network I/O), covering testing
scenarios #11 (redirect to a forbidden IP is blocked with no connection made) and the
DNS-rebinding TOCTOU closure the pinned transport exists for.
"""

import ipaddress

import httpx
import respx

from relay.domain.delivery_attempts import AttemptErrorClass
from relay.infra.http_sender import HttpxOutboundSender
from relay.infra.settings import get_settings
from relay.infra.ssrf_guard import Resolver

_PUBLIC_IP = "93.184.216.34"


def _map_resolver(mapping: dict[str, list[str]]) -> Resolver:
    """Resolves a hostname via a fixed lookup table, falling back to "IP literals resolve
    to themselves" (matching real getaddrinfo behavior) -- so a redirect Location that's
    already a raw IP (like the metadata address) doesn't need an entry in the map.
    """

    def _resolve(hostname: str) -> list[str]:
        if hostname in mapping:
            return mapping[hostname]
        try:
            ipaddress.ip_address(hostname)
            return [hostname]
        except ValueError:
            return []

    return _resolve


@respx.mock
async def test_redirect_to_forbidden_ip_is_blocked_with_no_connection_made() -> None:
    """Testing scenario #11: a destination redirects to 169.254.169.254 (the cloud
    metadata endpoint). The redirect hop itself is a real (mocked) request; the guard
    re-validates the Location target before any connection to it is attempted, and blocks
    it -- respx would raise AllMockedAssertionError if a request to the forbidden target
    were ever made, since no route is registered for it here.
    """
    resolver = _map_resolver({"attacker.example.com": [_PUBLIC_IP]})
    first_hop = respx.post(f"https://{_PUBLIC_IP}/webhook").mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://169.254.169.254/latest/meta-data/"}
        )
    )
    sender = HttpxOutboundSender(resolver=resolver)

    result = await sender.send(
        url="https://attacker.example.com/webhook", payload=b"{}", headers={}
    )
    await sender.aclose()

    assert result.error_class is AttemptErrorClass.SSRF_BLOCKED
    assert first_hop.call_count == 1  # the redirect response itself was real
    assert respx.calls.call_count == 1  # nothing else was ever requested


@respx.mock
async def test_dns_rebinding_connects_to_the_originally_validated_ip_not_a_fresh_lookup() -> None:
    """A fake resolver returns a public IP on the first lookup and a private IP on a
    second, simulating a record that rebinds mid-flight. One send() call only resolves
    once and connects to whatever it validated -- proving the pinned transport doesn't
    hand the hostname back to httpx and let it re-resolve, which is exactly the TOCTOU
    window IP pinning exists to close.
    """
    call_count = {"n": 0}

    def _rebinding_resolver(hostname: str) -> list[str]:
        call_count["n"] += 1
        return [_PUBLIC_IP] if call_count["n"] == 1 else ["10.0.0.5"]

    route = respx.post(f"https://{_PUBLIC_IP}/webhook").mock(
        return_value=httpx.Response(200, text="ok")
    )
    sender = HttpxOutboundSender(resolver=_rebinding_resolver)

    first = await sender.send(url="https://rebind.example.com/webhook", payload=b"{}", headers={})

    assert first.succeeded
    assert route.call_count == 1
    assert call_count["n"] == 1  # exactly one resolution for this one send() call

    # A second, independent attempt re-runs the guard from scratch (a fresh resolution --
    # simulating the record having since rebound) and is correctly blocked this time. The
    # guard isn't caching a stale "safe" verdict forever; it's the *in-flight* re-resolution
    # (mid-request) that pinning closes off, not re-validation across separate attempts.
    second = await sender.send(url="https://rebind.example.com/webhook", payload=b"{}", headers={})
    await sender.aclose()

    assert second.error_class is AttemptErrorClass.SSRF_BLOCKED
    assert call_count["n"] == 2


@respx.mock
async def test_pinned_transport_preserves_host_header_and_sets_sni_hostname() -> None:
    """The connection goes to the pinned IP, but from the destination's and TLS's point of
    view nothing looks different: Host header and SNI still name the original hostname.
    """
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["url_host"] = request.url.host
        captured["host_header"] = request.headers.get("host")
        captured["sni_hostname"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, text="ok")

    respx.post(f"https://{_PUBLIC_IP}/webhook").mock(side_effect=_capture)
    resolver = _map_resolver({"pinned.example.com": [_PUBLIC_IP]})
    sender = HttpxOutboundSender(resolver=resolver)

    result = await sender.send(url="https://pinned.example.com/webhook", payload=b"{}", headers={})
    await sender.aclose()

    assert result.succeeded
    assert captured["url_host"] == _PUBLIC_IP
    assert captured["host_header"] == "pinned.example.com"
    assert captured["sni_hostname"] == "pinned.example.com"


@respx.mock
async def test_redirect_to_an_allowed_destination_is_followed_and_revalidated() -> None:
    resolver = _map_resolver(
        {"start.example.com": [_PUBLIC_IP], "end.example.com": ["93.184.216.35"]}
    )
    first_hop = respx.post(f"https://{_PUBLIC_IP}/webhook").mock(
        return_value=httpx.Response(302, headers={"Location": "https://end.example.com/webhook"})
    )
    second_hop = respx.post("https://93.184.216.35/webhook").mock(
        return_value=httpx.Response(200, text="ok")
    )
    sender = HttpxOutboundSender(resolver=resolver)

    result = await sender.send(url="https://start.example.com/webhook", payload=b"{}", headers={})
    await sender.aclose()

    assert result.succeeded
    assert first_hop.call_count == 1
    assert second_hop.call_count == 1


@respx.mock
async def test_default_timeouts_and_user_agent_come_from_settings() -> None:
    """Phase 4: HttpxOutboundSender's connect/read timeouts and User-Agent default to
    Settings values (tightened from Phase 2/3's hardcoded 10s so the demo console's
    `slow-8s` mock reliably demonstrates the timeout path -- see
    docs/adr/0006-phase-4-demo-console.md) rather than module constants, and every
    outbound request carries the fixed, identifying User-Agent so an abuse report is
    traceable back to this service.
    """
    settings = get_settings()
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["user_agent"] = request.headers.get("user-agent")
        return httpx.Response(200, text="ok")

    respx.post(f"https://{_PUBLIC_IP}/webhook").mock(side_effect=_capture)
    resolver = _map_resolver({"ua.example.com": [_PUBLIC_IP]})
    sender = HttpxOutboundSender(resolver=resolver)

    assert sender._timeout.connect == settings.outbound_connect_timeout_seconds
    assert sender._timeout.read == settings.outbound_read_timeout_seconds

    result = await sender.send(url="https://ua.example.com/webhook", payload=b"{}", headers={})
    await sender.aclose()

    assert result.succeeded
    assert captured["user_agent"] == settings.outbound_user_agent


@respx.mock
async def test_caller_supplied_headers_take_precedence_over_default_user_agent() -> None:
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["user_agent"] = request.headers.get("user-agent")
        return httpx.Response(200, text="ok")

    respx.post(f"https://{_PUBLIC_IP}/webhook").mock(side_effect=_capture)
    resolver = _map_resolver({"ua2.example.com": [_PUBLIC_IP]})
    sender = HttpxOutboundSender(resolver=resolver)

    await sender.send(
        url="https://ua2.example.com/webhook",
        payload=b"{}",
        headers={"User-Agent": "custom-caller-agent"},
    )
    await sender.aclose()

    assert captured["user_agent"] == "custom-caller-agent"


@respx.mock
async def test_redirect_budget_exceeded_fails_as_http_error() -> None:
    resolver = _map_resolver({"loop.example.com": [_PUBLIC_IP]})
    route = respx.post(f"https://{_PUBLIC_IP}/webhook").mock(
        return_value=httpx.Response(302, headers={"Location": "https://loop.example.com/webhook"})
    )
    sender = HttpxOutboundSender(resolver=resolver)

    result = await sender.send(url="https://loop.example.com/webhook", payload=b"{}", headers={})
    await sender.aclose()

    assert result.error_class is AttemptErrorClass.HTTP_ERROR
    # MAX_REDIRECT_HOPS + 1 real requests: the initial attempt plus every followed hop.
    assert route.call_count == 4
