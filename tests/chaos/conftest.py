"""Fixtures for the chaos suite.

These tests kill and restart real containers of a real Relay stack. They are not per-PR CI
material -- they are slow, timing-sensitive, and need the full compose stack -- so they run
on demand and before releases:

    RELAY_E2E_BASE_URL=https://relay.bookr.tech uv run pytest tests/chaos

See `docs/adr/0009-phase-8-live-verification.md` for why crash recovery is proven by
killing processes rather than by injecting faults: the claims under test are about process
death, and a simulated network fault proves the code handles the simulation.
"""

from collections.abc import Iterator

import pytest

from tests import dockerctl
from tests.e2e.config import LiveConfig
from tests.e2e.live import AttemptFeed, EndpointHandle, LiveApi, Sandbox

CHAOS_SERVICES = ("api", "relay-worker", "dispatcher", "scheduler", "reaper", "postgres", "redis")


@pytest.fixture(scope="session")
def live_config() -> LiveConfig:
    return LiveConfig.from_env()


@pytest.fixture(scope="session")
def api(live_config: LiveConfig) -> Iterator[LiveApi]:
    client = LiveApi(live_config.base_url)
    yield client
    client.close()


@pytest.fixture(scope="session", autouse=True)
def _requires_stack(live_config: LiveConfig) -> None:
    if not dockerctl.stack_available():
        pytest.skip(
            "chaos tests need Docker access to the Relay stack's own containers "
            f"(looked for {dockerctl.container('api')})"
        )
    reason = live_config.undeliverable_reason
    if reason is not None:
        pytest.skip(f"chaos tests assert on real deliveries: {reason}")


@pytest.fixture(autouse=True)
def _leave_the_stack_as_we_found_it() -> Iterator[None]:
    """Every container back up after each test, pass or fail. A chaos suite that leaves a
    production stack half-dead when an assertion fails is worse than no chaos suite."""
    yield
    for service in CHAOS_SERVICES:
        dockerctl.ensure_running(dockerctl.container(service))


@pytest.fixture(scope="session")
def db() -> dockerctl.Psql:
    return dockerctl.psql()


@pytest.fixture(scope="session")
def sandbox(api: LiveApi) -> Sandbox:
    return api.provision_sandbox()


@pytest.fixture(scope="session")
def feed(api: LiveApi, live_config: LiveConfig, sandbox: Sandbox) -> Iterator[AttemptFeed]:
    stream = AttemptFeed(live_config.base_url, sandbox).start()
    yield stream
    stream.stop()


@pytest.fixture(scope="session")
def healthy_endpoint(api: LiveApi, live_config: LiveConfig, sandbox: Sandbox) -> EndpointHandle:
    return EndpointHandle.from_response(
        api.register_endpoint(sandbox, live_config.mock_url("always-200"), ["chaos.healthy"])
    )
