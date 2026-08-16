"""SSRF guard: resolves a destination hostname and validates the result isn't a network
this service should never be allowed to reach, closing off the classic "webhook URL that
actually points at the cloud metadata endpoint / an internal service / the box itself"
attack. See docs/adr/0005-phase-3-security-resilience.md for why post-resolution CIDR
checks plus IP pinning (relay.infra.pinned_transport) is the chosen shape, and
docs/guarantees.md for what this guard does and doesn't close off (DNS rebinding is
mitigated, not eliminated).

The deny list is fixed code, not settings -- these are security invariants, not something
a deployer should be able to accidentally weaken via an env var. Only the allowed *port*
set is configurable (relay.infra.settings.Settings.ssrf_allowed_ports).
"""

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# RFC 6598 "Shared Address Space" (carrier-grade NAT) -- ipaddress.is_private does not
# flag this range, so it needs its own explicit check.
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

# The AWS/GCP/Azure instance metadata IP -- the single most important address this guard
# exists to block, since it's how a naive SSRF hole turns into full cloud credential theft.
# Already covered by `is_link_local` (169.254.0.0/16), named here explicitly so the intent
# is on record rather than an accident of a broader range check.
_METADATA_IP = ipaddress.ip_address("169.254.169.254")

Resolver = Callable[[str], list[str]]


def default_resolver(hostname: str) -> list[str]:
    """Real DNS resolution via `socket.getaddrinfo`, deduplicated. Wrapped behind the
    `Resolver` alias (rather than called directly) so tests can inject a fake resolver --
    including one that returns a different address on a second call, to prove the pinned
    transport doesn't re-resolve mid-request (the DNS-rebinding TOCTOU window).
    """
    infos = socket.getaddrinfo(hostname, None)
    return sorted({str(info[4][0]) for info in infos})


@dataclass(frozen=True, slots=True)
class SsrfCheckResult:
    """The outcome of validating a URL. `resolved_ip` is only set when `allowed` is True --
    it's the exact address the IP-pinned transport must connect to, so the guard's
    validation and the actual connection target can never diverge.
    """

    allowed: bool
    hostname: str | None = None
    port: int | None = None
    resolved_ip: str | None = None
    reason: str | None = None


def _is_denied(ip: IpAddress) -> bool:
    if ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_reserved:
        return True
    if ip.is_multicast or ip.is_unspecified:
        return True
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_NETWORK:
        return True
    return ip == _METADATA_IP


def check_url(
    url: str,
    *,
    allowed_ports: frozenset[int],
    resolver: Resolver = default_resolver,
) -> SsrfCheckResult:
    """Validates a destination URL: scheme, port allow-list, then post-resolution CIDR
    checks against every address the hostname resolves to (not just the first) -- a
    hostname with multiple A/AAAA records is denied if *any* of them is forbidden, since an
    attacker controlling DNS could otherwise put a benign address first and a forbidden one
    second, hoping only the first gets checked.

    Deliberately synchronous and I/O-isolated to `resolver` alone: the caller (the http
    sender adapter) is responsible for running this off the event loop, since the default
    resolver does a real (blocking) DNS lookup.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        return SsrfCheckResult(allowed=False, reason=f"unsupported scheme: {parsed.scheme!r}")

    hostname = parsed.hostname
    if not hostname:
        return SsrfCheckResult(allowed=False, reason="url has no host")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in allowed_ports:
        return SsrfCheckResult(
            allowed=False, hostname=hostname, port=port, reason=f"port not allowed: {port}"
        )

    try:
        candidates = resolver(hostname)
    except OSError as exc:
        return SsrfCheckResult(
            allowed=False, hostname=hostname, port=port, reason=f"dns resolution failed: {exc}"
        )
    if not candidates:
        return SsrfCheckResult(
            allowed=False,
            hostname=hostname,
            port=port,
            reason="dns resolution returned no addresses",
        )

    for candidate in candidates:
        ip = ipaddress.ip_address(candidate)
        if _is_denied(ip):
            return SsrfCheckResult(
                allowed=False,
                hostname=hostname,
                port=port,
                resolved_ip=candidate,
                reason=f"address denied: {candidate}",
            )

    # Pin to the first validated candidate -- deterministic, and the whole point is to
    # connect to *this* address rather than let the HTTP client re-resolve later.
    return SsrfCheckResult(allowed=True, hostname=hostname, port=port, resolved_ip=candidates[0])
