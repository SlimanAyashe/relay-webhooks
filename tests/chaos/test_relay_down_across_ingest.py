"""Scenario #1, at process level: the relay is dead between the database commit and the
Redis publish.

`tests/integration/test_relay_worker.py::test_outbox_row_committed_before_a_relay_run_is_recovered_on_the_next_run`
makes this argument by simply not running the relay. This makes it by killing the relay
container -- so what is being asserted is that a `202` returned by the deployed API is not a
lie even when the process responsible for fanning that event out no longer exists.
"""

import time

from tests import dockerctl
from tests.e2e.live import AttemptFeed, EndpointHandle, LiveApi, Sandbox

# Long enough that "the relay would have picked it up by now" is a fair statement: the
# outbox poll interval is 1s.
STUCK_OBSERVATION_SECONDS = 5.0


def test_an_event_accepted_while_the_relay_is_dead_is_delivered_once_it_returns(
    api: LiveApi,
    sandbox: Sandbox,
    feed: AttemptFeed,
    healthy_endpoint: EndpointHandle,
    db: dockerctl.Psql,
) -> None:
    relay = dockerctl.container("relay-worker")
    dockerctl.stop(relay)
    assert not dockerctl.is_running(relay)

    # The API is untouched by the relay being gone: ingest commits the event and its outbox
    # row in one transaction and returns 202 without ever talking to the relay.
    response = api.trigger_accepted(
        sandbox, event_type="chaos.healthy", payload={"probe": "relay-down"}
    )
    assert response.status_code == 202
    event_id = response.json()["id"]

    time.sleep(STUCK_OBSERVATION_SECONDS)

    # ...and with nothing running to publish it, the promise is held entirely by Postgres:
    # a pending outbox row, no delivery, nothing on the stream.
    assert db.scalar(f"SELECT status FROM outbox WHERE event_id = '{event_id}'") == "pending"
    assert db.count("deliveries", f"event_id = '{event_id}'") == 0

    dockerctl.start(relay)
    dockerctl.wait_running(relay)

    attempt = feed.wait_for(
        lambda e: e["event_id"] == event_id and e["delivery_state"] == "delivered",
        timeout=90.0,
        description="the recovered event to be delivered",
    )

    assert attempt["response_status"] == 200
    assert db.scalar(f"SELECT status FROM outbox WHERE event_id = '{event_id}'") == "processed"
    assert db.count("deliveries", f"event_id = '{event_id}'") == 1
