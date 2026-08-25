"""Environment-driven configuration for the live suites (tests/e2e and tests/chaos).

The same suite runs against local compose and against production -- the only difference
is `RELAY_E2E_BASE_URL`. Nothing here imports `relay.*`: these tests talk to the deployed
service through its public HTTP surface exactly as any other client would, so an import
of the application's own code would quietly turn a live test back into an in-process one.
"""

import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

DEFAULT_BASE_URL = "http://localhost:8000"

# Ports the SSRF guard allows outbound (relay.infra.settings.ssrf_allowed_ports default).
# Duplicated as a literal rather than imported, deliberately -- see the module docstring.
_DELIVERABLE_PORTS = {80, 443}


@dataclass(frozen=True, slots=True)
class LiveConfig:
    """Where the live suite points, and what it is therefore able to prove.

    `base_url` is where the API is called; `receiver_base_url` is the origin used when
    registering a built-in `/mock/*` destination. They differ only when the API is reached
    over a private/loopback address (a local compose run) while deliveries still need a
    publicly-resolvable https origin -- Relay's own SSRF guard blocks a destination that
    resolves to loopback/RFC1918 or targets a port outside 80/443, which is exactly the
    guarantee the rest of this suite exists to prove, so it is never worked around here.
    """

    base_url: str
    receiver_base_url: str

    @classmethod
    def from_env(cls) -> "LiveConfig":
        base_url = os.environ.get("RELAY_E2E_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        receiver = os.environ.get("RELAY_E2E_RECEIVER_BASE_URL", base_url).rstrip("/")
        return cls(base_url=base_url, receiver_base_url=receiver)

    def mock_url(self, name: str) -> str:
        """URL of a built-in mock receiver, e.g. `mock_url("always-200")`."""
        return f"{self.receiver_base_url}/mock/{name}"

    @property
    def undeliverable_reason(self) -> str | None:
        """Why a delivery to a `/mock/*` receiver could not possibly succeed against this
        configuration, or None if it can. Tests that assert on a *delivered* outcome skip
        with this string rather than failing, so pointing the suite at a loopback dev stack
        reports "this config can't prove delivery" instead of a misleading red test.
        """
        return _delivery_blocker(self.receiver_base_url)


def _delivery_blocker(receiver_base_url: str) -> str | None:
    parsed = urlparse(receiver_base_url)
    if parsed.scheme != "https":
        return (
            f"receiver base url {receiver_base_url!r} is not https -- endpoint "
            "registration requires https, so no delivery can be proven from here; set "
            "RELAY_E2E_RECEIVER_BASE_URL to the deployment's public https origin"
        )
    host = parsed.hostname or ""
    port = parsed.port or 443
    if port not in _DELIVERABLE_PORTS:
        return (
            f"receiver base url {receiver_base_url!r} targets port {port}, outside the "
            "SSRF guard's allowed ports (80/443) -- Relay would correctly refuse to "
            "deliver there"
        )
    try:
        resolved = ipaddress.ip_address(socket.gethostbyname(host))
    except (OSError, ValueError) as exc:
        return f"receiver host {host!r} does not resolve ({exc})"
    if not resolved.is_global:
        return (
            f"receiver host {host!r} resolves to {resolved}, which Relay's SSRF guard "
            "correctly blocks -- point RELAY_E2E_RECEIVER_BASE_URL at the deployment's "
            "public origin to prove delivery"
        )
    return None
