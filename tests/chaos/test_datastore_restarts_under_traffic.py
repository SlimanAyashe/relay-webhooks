"""Restart Postgres, then Redis, while events are being accepted -- and lose none of them.

The bar is deliberately asymmetric, and it is the bar `docs/guarantees.md` actually sets:
duplicates are acceptable (at-least-once), a request that failed outright is acceptable
(it was never accepted, and the caller knows), and an event that got a `202` and then
quietly evaporated is not.
"""

import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest

from tests import dockerctl
from tests.e2e.config import LiveConfig
from tests.e2e.live import EndpointHandle, LiveApi, Sandbox

TRICKLE_EVENT_TYPE = "chaos.trickle"
TRICKLE_EVENTS = 12
TRICKLE_INTERVAL_SECONDS = 4.0

# When to pull each dependency out from under the traffic, in seconds from the first event.
POSTGRES_RESTART_AT = 8.0
REDIS_RESTART_AT = 28.0

RECONCILE_TIMEOUT_SECONDS = 300.0
TERMINAL_STATES = ("delivered", "dead")

# How many consecutive green /readyz responses count as "the deployment has recovered".
# More than one on purpose: the first green answer can come from a healthy pooled connection
# while stale ones are still queued behind it (see _wait_until_consistently_ready).
CONSECUTIVE_READY_RESPONSES = 3
RECOVERY_TIMEOUT_SECONDS = 180.0


@dataclass
class Trickle:
    """What actually happened while the datastores were being restarted."""

    accepted: list[str] = field(default_factory=list)
    rejected: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@pytest.fixture(scope="session")
def trickle_sandbox(api: LiveApi) -> Sandbox:
    """Its own tenant and event budget -- this test spends most of a sandbox's event cap."""
    return api.provision_sandbox()


@pytest.fixture(scope="session")
def trickle_endpoint(
    api: LiveApi, live_config: LiveConfig, trickle_sandbox: Sandbox
) -> EndpointHandle:
    return EndpointHandle.from_response(
        api.register_endpoint(
            trickle_sandbox, live_config.mock_url("always-200"), [TRICKLE_EVENT_TYPE]
        )
    )


@pytest.fixture
def trickle(api: LiveApi, trickle_sandbox: Sandbox) -> Iterator[Trickle]:
    """A steady, slow stream of ingest requests in the background, recording exactly which
    ones the service promised to deliver."""
    result = Trickle()
    stop = threading.Event()

    def run() -> None:
        for index in range(TRICKLE_EVENTS):
            if stop.is_set():
                return
            try:
                response = api.trigger(
                    trickle_sandbox,
                    event_type=TRICKLE_EVENT_TYPE,
                    payload={"probe": "restart-under-traffic", "n": index},
                )
                if response.status_code == 202:
                    result.accepted.append(response.json()["id"])
                else:
                    result.rejected.append(response.status_code)
            except Exception as exc:
                result.errors.append(repr(exc))
            stop.wait(TRICKLE_INTERVAL_SECONDS)

    thread = threading.Thread(target=run, name="chaos-trickle", daemon=True)
    thread.start()
    yield result
    stop.set()
    thread.join(timeout=30.0)


def _wait_until_consistently_ready(api: LiveApi) -> int:
    """Polls /readyz until it answers green several times in a row, and returns how many
    non-green answers it took to get there.

    This is an assertion in its own right -- the deployment has to come *back*, not merely
    survive -- and it is also where a real defect shows up. `relay.infra.redis` builds one
    pooled client with no `health_check_interval`, so after Redis restarts the pool still
    holds connections the server has already closed. Each one raises
    `ConnectionError: Connection closed by server` the first time it is drawn, and the pool
    only cleans itself up by handing those out and failing. Polling here drains them
    deliberately rather than leaving them to poison whatever request comes next.

    Expect this to return 0 most runs, and do not read that as "no stale connections": the
    trickle above is still going while Redis comes back, so it usually absorbs them first --
    each one as a 500 to a caller. `trickle.rejected` is where that cost actually shows up,
    which is why the test prints both.
    """
    deadline = time.monotonic() + RECOVERY_TIMEOUT_SECONDS
    failures = 0
    streak = 0
    while time.monotonic() < deadline:
        if api.get("/readyz").status_code == 200:
            streak += 1
            if streak >= CONSECUTIVE_READY_RESPONSES:
                return failures
        else:
            failures += 1
            streak = 0
        time.sleep(1.0)
    raise AssertionError(
        f"/readyz never answered green {CONSECUTIVE_READY_RESPONSES}x in a row within "
        f"{RECOVERY_TIMEOUT_SECONDS}s after both datastores were restarted "
        f"({failures} failed responses)"
    )


def test_restarting_postgres_and_redis_under_traffic_loses_no_accepted_event(
    api: LiveApi,
    trickle: Trickle,
    trickle_endpoint: EndpointHandle,
    db: dockerctl.Psql,
) -> None:
    started = time.monotonic()

    time.sleep(max(0.0, POSTGRES_RESTART_AT - (time.monotonic() - started)))
    dockerctl.restart(dockerctl.container("postgres"))

    time.sleep(max(0.0, REDIS_RESTART_AT - (time.monotonic() - started)))
    dockerctl.restart(dockerctl.container("redis"))

    time.sleep(max(0.0, TRICKLE_EVENTS * TRICKLE_INTERVAL_SECONDS - (time.monotonic() - started)))
    accepted = list(trickle.accepted)

    assert accepted, (
        "nothing was accepted at all during the run -- rejected: "
        f"{trickle.rejected}, errors: {trickle.errors[:3]}"
    )
    # The API is expected to fail requests outright while a dependency is down; that is
    # failing closed, not losing data, and those events were never promised to anyone.
    assert accepted or trickle.rejected

    stale_connection_errors = _wait_until_consistently_ready(api)
    print(
        f"\ningest rejections during the run: {sorted(trickle.rejected)}"
        f"\n/readyz needed {stale_connection_errors} further failed response(s) before "
        "answering green consistently"
    )

    unresolved = _wait_for_terminal_states(db, accepted, timeout=RECONCILE_TIMEOUT_SECONDS)

    assert unresolved == [], (
        f"{len(unresolved)} of {len(accepted)} accepted events never reached a terminal "
        f"delivery state after recovery: {unresolved[:5]}"
    )


def _wait_for_terminal_states(
    db: dockerctl.Psql, event_ids: list[str], *, timeout: float
) -> list[str]:
    """Returns the accepted events that still have no delivery in a terminal state.

    A missing *delivery row* is the loss case this test exists to catch: it means the event
    was committed and acknowledged but never fanned out, with nothing left to notice it.
    """
    deadline = time.monotonic() + timeout
    quoted = ",".join(f"'{event_id}'" for event_id in event_ids)
    states = "','".join(TERMINAL_STATES)
    outstanding: list[str] = list(event_ids)
    while time.monotonic() < deadline and outstanding:
        rows = db.rows(
            f"SELECT event_id FROM deliveries WHERE event_id IN ({quoted}) "
            f"AND state IN ('{states}')"
        )
        settled = {row[0] for row in rows}
        outstanding = [event_id for event_id in event_ids if event_id not in settled]
        if outstanding:
            time.sleep(5.0)
    return outstanding
