"""Fixtures for the live smoke suite.

Not collected by `pytest` on its own -- `pyproject.toml`'s `testpaths` covers unit and
integration only, so this suite runs when it is asked for:

    RELAY_E2E_BASE_URL=https://relay.bookr.tech uv run pytest tests/e2e

Sandboxes are session-scoped and shared. `POST /v1/sandbox` is rate-limited per client IP
(~1 per 20s) precisely to stop scripted key farming, and this suite is a script; provisioning
one per test would spend the whole run waiting on a control it also asserts.
"""

from collections.abc import Iterator

import pytest

from tests import dockerctl
from tests.e2e.config import LiveConfig
from tests.e2e.live import AttemptFeed, EndpointHandle, LiveApi, Sandbox

DEMO_EVENT_TYPE = "demo.triggered"
# A second event type so a test that needs a failing destination doesn't also fan out to
# the happy-path endpoint (and burn its circuit breaker) on every trigger.
FAILING_EVENT_TYPE = "demo.failing"


@pytest.fixture(scope="session")
def live_config() -> LiveConfig:
    return LiveConfig.from_env()


@pytest.fixture(scope="session")
def api(live_config: LiveConfig) -> Iterator[LiveApi]:
    client = LiveApi(live_config.base_url)
    yield client
    client.close()


@pytest.fixture(scope="session", autouse=True)
def _preflight(api: LiveApi, live_config: LiveConfig) -> None:
    """Fails loudly, once, if the target isn't up -- a live suite that quietly skips
    everything because nothing was listening is worse than no live suite."""
    response = api.get("/healthz")
    assert response.status_code == 200, (
        f"{live_config.base_url}/healthz returned {response.status_code}; "
        "is RELAY_E2E_BASE_URL pointing at a running deployment?"
    )


@pytest.fixture(scope="session")
def sandbox(api: LiveApi) -> Sandbox:
    """The suite's main tenant: everything that doesn't need a *second* identity or a
    deliberately exhausted quota shares this one."""
    return api.provision_sandbox()


@pytest.fixture(scope="session")
def other_sandbox(api: LiveApi) -> Sandbox:
    """A second live tenant, for the cross-tenant isolation checks."""
    return api.provision_sandbox()


@pytest.fixture(scope="session")
def feed(api: LiveApi, live_config: LiveConfig, sandbox: Sandbox) -> Iterator[AttemptFeed]:
    """The live attempt timeline for `sandbox`, opened once for the whole session --
    the same SSE stream the demo console renders."""
    stream = AttemptFeed(live_config.base_url, sandbox).start()
    yield stream
    stream.stop()


@pytest.fixture(scope="session")
def deliverable(live_config: LiveConfig) -> None:
    """Skips a test that asserts on a *successful delivery* when the configured receiver
    origin is one Relay's own SSRF guard would (correctly) refuse to deliver to."""
    reason = live_config.undeliverable_reason
    if reason is not None:
        pytest.skip(reason)


@pytest.fixture(scope="session")
def happy_endpoint(
    api: LiveApi, live_config: LiveConfig, sandbox: Sandbox, deliverable: None
) -> EndpointHandle:
    """One endpoint pointed at the `always-200` mock, shared by every happy-path test."""
    return EndpointHandle.from_response(
        api.register_endpoint(sandbox, live_config.mock_url("always-200"), [DEMO_EVENT_TYPE])
    )


@pytest.fixture(scope="session")
def failing_endpoint(
    api: LiveApi, live_config: LiveConfig, sandbox: Sandbox, deliverable: None
) -> EndpointHandle:
    """One endpoint pointed at the `always-500` mock: the retry/backoff/breaker/DLQ path."""
    return EndpointHandle.from_response(
        api.register_endpoint(sandbox, live_config.mock_url("always-500"), [FAILING_EVENT_TYPE])
    )


@pytest.fixture(scope="session")
def requires_docker() -> None:
    """For the handful of promises that cannot be observed from outside the host at all --
    container stdout being JSON, the dispatcher's own metrics port. Skipped, never faked,
    when the suite runs from somewhere without access to the stack's Docker daemon."""
    if not dockerctl.stack_available():
        pytest.skip("no local Docker access to the Relay stack")
