"""Scenario #4 as a real crash: `kill -9` a dispatcher while an HTTP delivery is in flight.

The receiver has already been handed the request when the worker dies, and the stream
message was never acked -- so the reaper's `XAUTOCLAIM` sweep reclaims it under its
*original* message id and delivers it again. The receiver sees the same event twice.

That duplicate is the point. It is not a bug being tolerated, it is the at-least-once
guarantee made concrete: exactly-once external side effects are impossible here, which is
why `docs/guarantees.md` says receivers must be idempotent.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from tests import dockerctl
from tests.e2e.config import LiveConfig
from tests.e2e.live import AttemptFeed, EndpointHandle, LiveApi, Sandbox

SLOW_EVENT_TYPE = "chaos.slow"

# The mock sleeps 8s before answering and the dispatcher gives up reading at 5s, so the
# request is genuinely in flight for the first five seconds. Kill early in that window: the
# poll below can notice the claim up to POLL_INTERVAL_SECONDS late, and killing after the
# dispatcher has already recorded a timeout would test nothing.
KILL_AFTER_SECONDS = 1.5
POLL_INTERVAL_SECONDS = 0.25

# reaper_min_idle_ms (30s) + reaper_tick_interval_seconds (30s), plus room for the
# reclaimed attempt itself to run.
RECLAIM_TIMEOUT_SECONDS = 180.0


@pytest.fixture(scope="session")
def slow_endpoint(api: LiveApi, live_config: LiveConfig, sandbox: Sandbox) -> EndpointHandle:
    return EndpointHandle.from_response(
        api.register_endpoint(sandbox, live_config.mock_url("slow-8s"), [SLOW_EVENT_TYPE])
    )


def _receiver_hits(path: str, *, since: str) -> int:
    """How many requests the receiver itself *finished* -- the mock receivers are served by
    the api container, so its own request log is the receiver-side record.

    Finished, not received: the line is written on completion, which for `slow-8s` is eight
    seconds after the request arrives. So this cannot show that the receiver is currently
    holding a request -- at the moment of the kill it has the request and has logged nothing
    about it. Evidence of in-flight is the pending-entries list; this is evidence of the
    duplicate, afterwards.
    """
    logs = dockerctl.logs_since(dockerctl.container("api"), since)
    return sum(1 for line in logs.splitlines() if f'"path": "{path}"' in line)


def _wait_for[T](
    predicate: Callable[[], T | None],
    *,
    timeout: float,
    description: str,
    interval: float = 1.0,
) -> T:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s waiting for {description}")


def test_a_killed_dispatcher_is_reclaimed_under_the_same_id_and_the_receiver_sees_a_duplicate(
    api: LiveApi,
    live_config: LiveConfig,
    sandbox: Sandbox,
    feed: AttemptFeed,
    slow_endpoint: EndpointHandle,
    db: dockerctl.Psql,
) -> None:
    dispatcher = dockerctl.container("dispatcher")
    # Bound the receiver-log window to this test's own traffic: a previous run's delivery may
    # still be working through its retry chain against the same mock.
    since = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    hits_before = _receiver_hits("/mock/slow-8s", since=since)

    event_id = api.trigger_accepted(
        sandbox, event_type=SLOW_EVENT_TYPE, payload={"probe": "kill-mid-delivery"}
    ).json()["id"]

    delivery_id = _wait_for(
        lambda: db.scalar(f"SELECT id FROM deliveries WHERE event_id = '{event_id}'") or None,
        timeout=60.0,
        description="the delivery row to be fanned out",
    )
    message_id = _wait_for(
        lambda: dockerctl.message_id_for_delivery(delivery_id),
        timeout=30.0,
        description="the delivery to reach the Redis stream",
    )
    claimed = _wait_for(
        lambda: next((e for e in dockerctl.pending_entries() if e.message_id == message_id), None),
        timeout=60.0,
        description="a dispatcher to claim the message",
        interval=POLL_INTERVAL_SECONDS,
    )
    assert claimed.consumer.startswith("dispatcher")

    # The dispatcher holds the message, so its HTTP request is on the wire and the receiver
    # is sitting on it. Kill the worker where it stands: no SIGTERM, no graceful shutdown, no
    # chance to ack.
    time.sleep(KILL_AFTER_SECONDS)
    dockerctl.kill(dispatcher)

    still_pending = next(
        (e for e in dockerctl.pending_entries() if e.message_id == message_id), None
    )
    assert still_pending is not None, "a killed consumer's message must stay in the PEL"
    assert still_pending.consumer == claimed.consumer
    assert db.count("delivery_attempts", f"delivery_id = '{delivery_id}'") == 0, (
        "the worker died before it could record the attempt -- that is the whole premise"
    )

    dockerctl.ensure_running(dispatcher)

    # The reaper reclaims the entry under its *original* message id -- visible while it
    # holds it (the entry's consumer becomes the reaper, its delivery count increments),
    # and conclusive once that same id is acked and gone: a reaper that had re-queued a new
    # message instead would have left this id pending forever.
    reclaim_evidence: list[dockerctl.PendingEntry] = []

    def acked_after_reclaim() -> str | None:
        entry = next((e for e in dockerctl.pending_entries() if e.message_id == message_id), None)
        if entry is None:
            return "acked"
        reclaim_evidence.append(entry)
        return None

    _wait_for(
        acked_after_reclaim,
        timeout=RECLAIM_TIMEOUT_SECONDS,
        description="the abandoned message to be reclaimed and acked under its original id",
    )
    assert any(
        e.consumer.startswith("reaper") or e.delivery_count > claimed.delivery_count
        for e in reclaim_evidence
    ), f"no sign the reaper ever took over {message_id}: {reclaim_evidence}"
    assert db.count("delivery_attempts", f"delivery_id = '{delivery_id}'") >= 1, (
        "the reclaimed message must have been reprocessed, not just acked"
    )

    # The receiver got the same event a second time. This is the duplicate the at-least-once
    # guarantee promises, observed at the receiver rather than argued about.
    _wait_for(
        lambda: _receiver_hits("/mock/slow-8s", since=since) >= hits_before + 2,
        timeout=RECLAIM_TIMEOUT_SECONDS,
        description="the redelivery to reach the receiver",
    )

    # ...and the event is not lost: once the destination stops being pathological, the
    # retry chain that the reclaim restarted carries it to a successful delivery.
    api.repoint_endpoint(sandbox, slow_endpoint.id, live_config.mock_url("always-200"))
    feed.wait_for(
        lambda e: e["event_id"] == event_id and e["delivery_state"] == "delivered",
        timeout=300.0,
        description="the reclaimed event to be delivered once the destination recovers",
    )
