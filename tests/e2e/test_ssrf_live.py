"""Live SSRF probes: register forbidden destinations on the deployed service and watch it
refuse them.

`tests/integration/test_http_sender_ssrf.py` already proves the guard's logic against
`respx`. What it cannot prove is that the guard is actually wired into the deployment
serving the public domain -- that the production process really does re-resolve and re-check
every attempt, and that nothing in the reverse proxy, the environment, or the image build
quietly bypassed it. That is what these probes are for.

Each probe gets its own endpoint: a blocked destination fails, and five failures in a row
against one endpoint would open its circuit breaker and start deferring attempts instead of
making them, which would prove nothing about the guard.
"""

from collections.abc import Iterator
from typing import Any, NamedTuple

import pytest

from tests import dockerctl
from tests.e2e.config import LiveConfig
from tests.e2e.live import AttemptFeed, LiveApi, Sandbox

# A blocked attempt never opens a socket, so it comes back in milliseconds. A guard that
# had been bypassed would instead hang on a black-holed private address until the 5s
# connect timeout and be classified `timeout` -- so this bound is part of the assertion,
# not a performance check.
NO_CONNECTION_LATENCY_CEILING_MS = 2500


class Probe(NamedTuple):
    name: str
    url_template: str
    event_type: str
    forbidden_address: str


PROBES = (
    Probe("loopback", "https://127.0.0.1/webhook", "ssrf.loopback", "127.0.0.1"),
    Probe("rfc1918", "https://10.0.0.1/webhook", "ssrf.rfc1918", "10.0.0.1"),
    Probe(
        "metadata",
        "https://169.254.169.254/latest/meta-data/",
        "ssrf.metadata",
        "169.254.169.254",
    ),
    # A genuinely public hostname whose A record points at a forbidden address -- the
    # rebinding shape, and the case a naive "is the hostname suspicious?" check misses
    # entirely. nip.io resolves <ip>.nip.io to <ip>.
    Probe(
        "public-hostname-resolving-to-loopback",
        "https://127.0.0.1.nip.io/webhook",
        "ssrf.rebind",
        "127.0.0.1",
    ),
    # A destination that is perfectly legitimate at registration time and only redirects to
    # the metadata address once Relay connects to it.
    Probe(
        "redirect-to-metadata",
        "{receiver}/mock/redirect-to-metadata",
        "ssrf.redirect",
        "169.254.169.254",
    ),
)

# A sandbox is capped at 3 endpoints, so five probes need two of them.
_PROBES_PER_SANDBOX = 3


@pytest.fixture(scope="session")
def ssrf_attempts(
    api: LiveApi, live_config: LiveConfig, deliverable: None
) -> Iterator[dict[str, dict[str, Any]]]:
    """Runs every probe once against the deployment and returns the first delivery attempt
    each one produced, keyed by probe name."""
    sandboxes: list[Sandbox] = []
    feeds: list[AttemptFeed] = []
    attempts: dict[str, dict[str, Any]] = {}
    try:
        for chunk_start in range(0, len(PROBES), _PROBES_PER_SANDBOX):
            chunk = PROBES[chunk_start : chunk_start + _PROBES_PER_SANDBOX]
            sandbox = api.provision_sandbox()
            sandboxes.append(sandbox)
            feed = AttemptFeed(live_config.base_url, sandbox).start()
            feeds.append(feed)

            event_ids: dict[str, str] = {}
            for probe in chunk:
                url = probe.url_template.format(receiver=live_config.receiver_base_url)
                api.register_endpoint(sandbox, url, [probe.event_type])
                response = api.trigger_accepted(
                    sandbox, event_type=probe.event_type, payload={"probe": probe.name}
                )
                event_ids[probe.name] = response.json()["id"]

            for probe in chunk:
                attempts[probe.name] = feed.wait_for(
                    lambda e, wanted=event_ids[probe.name]: e["event_id"] == wanted,
                    timeout=90.0,
                    description=f"the {probe.name} probe's first delivery attempt",
                )
        yield attempts
    finally:
        for feed in feeds:
            feed.stop()


@pytest.mark.parametrize("probe", PROBES, ids=[p.name for p in PROBES])
def test_a_forbidden_destination_is_blocked_with_no_connection_made(
    probe: Probe, ssrf_attempts: dict[str, dict[str, Any]]
) -> None:
    attempt = ssrf_attempts[probe.name]

    assert attempt["error_class"] == "ssrf_blocked", (
        f"{probe.name}: expected the guard to block this destination, got "
        f"error_class={attempt['error_class']!r} status={attempt['response_status']!r}"
    )
    assert attempt["response_status"] is None, (
        f"{probe.name}: a response status means something answered -- the request reached "
        "the forbidden address"
    )
    assert attempt["latency_ms"] <= NO_CONNECTION_LATENCY_CEILING_MS, (
        f"{probe.name}: {attempt['latency_ms']}ms is long enough to be a connection "
        "attempt, not a pre-flight rejection"
    )


@pytest.mark.parametrize("probe", PROBES, ids=[p.name for p in PROBES])
def test_a_blocked_destination_is_retried_not_silently_dropped(
    probe: Probe, ssrf_attempts: dict[str, dict[str, Any]]
) -> None:
    """A blocked attempt is a failed attempt, not a discarded delivery: it stays in the
    retry chain (and eventually the DLQ) where an operator can see it."""
    assert ssrf_attempts[probe.name]["delivery_state"] in {"retrying", "dead"}


def test_the_redirect_probe_reached_the_mock_but_never_the_metadata_address(
    ssrf_attempts: dict[str, dict[str, Any]], requires_docker: None
) -> None:
    """Pins where the block happened: Relay connected to the (legitimate, public) mock
    receiver -- its access log line proves that -- and stopped at the `Location` hop rather
    than following it."""
    api_logs = dockerctl.logs_since(dockerctl.container("api"), "30m")

    assert "/mock/redirect-to-metadata" in api_logs
    assert ssrf_attempts["redirect-to-metadata"]["error_class"] == "ssrf_blocked"
