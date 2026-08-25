"""Authentication and tenant isolation, exercised through the deployed auth path.

`tests/integration/test_tenant_isolation.py` proves the service layer scopes every query by
tenant. This proves the *deployment* does: same keys, same 401/404 answers, over the real
proxy, against the real database, with two tenants that genuinely exist in production.
"""

import uuid

import pytest

from tests import dockerctl
from tests.e2e.config import LiveConfig
from tests.e2e.live import AttemptFeed, EndpointHandle, LiveApi, Sandbox

INVALID_KEY_DETAIL = "missing or invalid API key"

# How long to watch a second tenant's stream while the first tenant's deliveries are
# happening. Long enough that a leak would have shown up; short enough to stay a smoke test.
CROSS_TENANT_SILENCE_SECONDS = 20.0


def test_missing_malformed_and_unknown_keys_are_all_rejected_the_same_way(
    api: LiveApi,
) -> None:
    """The same generic 401 detail for all three: a response must never let a caller tell
    "this key doesn't exist" apart from "this key exists but isn't valid"."""
    responses = {
        "missing": api.get("/v1/endpoints"),
        "malformed": api.get("/v1/endpoints", headers={"X-API-Key": "not-a-key"}),
        "unknown": api.get(
            "/v1/endpoints", headers={"X-API-Key": f"relay_{uuid.uuid4().hex}.{uuid.uuid4().hex}"}
        ),
    }

    for name, response in responses.items():
        assert response.status_code == 401, f"{name}: {response.status_code} {response.text}"
        assert response.json()["detail"] == INVALID_KEY_DETAIL, name


def test_one_tenants_key_cannot_read_another_tenants_endpoint(
    api: LiveApi, sandbox: Sandbox, other_sandbox: Sandbox, happy_endpoint: EndpointHandle
) -> None:
    """404, never 403: tenant B's key can't distinguish "no such endpoint" from "someone
    else's endpoint", so existence never leaks across the tenant boundary."""
    mine = api.get(f"/v1/endpoints/{happy_endpoint.id}", headers=sandbox.headers)
    theirs = api.get(f"/v1/endpoints/{happy_endpoint.id}", headers=other_sandbox.headers)

    assert mine.status_code == 200
    assert theirs.status_code == 404


def test_one_tenants_key_cannot_replay_another_tenants_delivery(
    api: LiveApi,
    sandbox: Sandbox,
    other_sandbox: Sandbox,
    feed: AttemptFeed,
    happy_endpoint: EndpointHandle,
) -> None:
    """The pair of answers is the proof: the owner gets 409 (it exists, it just isn't
    dead), the stranger gets 404 (as far as they can tell, it doesn't exist at all)."""
    event_id = api.trigger_accepted(sandbox, payload={"probe": "isolation"}).json()["id"]
    attempt = feed.wait_for(
        lambda e: e["event_id"] == event_id,
        timeout=60.0,
        description="a delivery belonging to the first tenant",
    )
    delivery_id = attempt["delivery_id"]

    owner = api.replay(sandbox, delivery_id)
    stranger = api.replay(other_sandbox, delivery_id)

    assert owner.status_code == 409, owner.text
    assert stranger.status_code == 404, stranger.text


def test_a_second_tenant_sees_none_of_the_first_tenants_deliveries(
    api: LiveApi, sandbox: Sandbox, other_sandbox: Sandbox, happy_endpoint: EndpointHandle
) -> None:
    assert api.dlq(other_sandbox) == []
    listed = api.get("/v1/endpoints", headers=other_sandbox.headers).json()["items"]
    assert [item["id"] for item in listed] == []
    assert api.sandbox_metrics(other_sandbox)["sample_size"] == 0


def test_the_attempt_stream_carries_only_the_subscribing_tenants_attempts(
    api: LiveApi,
    live_config: LiveConfig,
    sandbox: Sandbox,
    other_sandbox: Sandbox,
    feed: AttemptFeed,
    happy_endpoint: EndpointHandle,
) -> None:
    """One Redis Pub/Sub channel per tenant id, so this isn't a filter that could be
    forgotten -- another tenant's events physically never arrive on this subscription."""
    eavesdropper = AttemptFeed(live_config.base_url, other_sandbox).start()
    try:
        event_id = api.trigger_accepted(sandbox, payload={"probe": "stream-isolation"}).json()["id"]
        feed.wait_for(
            lambda e: e["event_id"] == event_id,
            timeout=CROSS_TENANT_SILENCE_SECONDS,
            description="the first tenant's own attempt",
        )
        assert eavesdropper.events == []
    finally:
        eavesdropper.stop()


def test_a_revoked_key_is_rejected_on_the_very_next_request(
    api: LiveApi, requires_docker: None
) -> None:
    """Revocation itself has no public route (keys are issued by provisioning and revoked
    operationally), so it is applied in the deployed database -- but the rejection is
    asserted over the real HTTP auth path, which is the part that could regress."""
    doomed = api.provision_sandbox()
    db = dockerctl.psql()
    assert api.get("/v1/endpoints", headers=doomed.headers).status_code == 200

    db.execute(f"UPDATE api_keys SET revoked_at = now() WHERE tenant_id = '{doomed.tenant_id}'")

    after = api.get("/v1/endpoints", headers=doomed.headers)
    assert after.status_code == 401
    assert after.json()["detail"] == INVALID_KEY_DETAIL


@pytest.mark.parametrize("path", ["/v1/dlq", "/v1/sandbox/metrics", "/v1/endpoints"])
def test_every_read_route_requires_a_key(api: LiveApi, path: str) -> None:
    assert api.get(path).status_code == 401
