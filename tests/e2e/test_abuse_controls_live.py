"""The controls that make a public, self-serve outbound HTTP proxy safe to leave on the
internet, asserted against the deployed console API rather than against Settings.

Every one of these is enforced server-side regardless of what the console UI sends, which
is precisely the thing a live test can prove and an in-process one cannot: the deployed
process really is running with the sandbox limits, not a permissive local default.
"""

import uuid

import httpx
import pytest

from tests import dockerctl
from tests.e2e.config import LiveConfig
from tests.e2e.live import LiveApi, Sandbox

# relay.infra.settings: event_payload_max_bytes = 65_536.
OVERSIZED_PAYLOAD_BYTES = 65_536 + 1_024

SANDBOX_CREATION_PROBE_ATTEMPTS = 8


@pytest.fixture(scope="session")
def abuse_sandbox(api: LiveApi) -> Sandbox:
    """Its own tenant: these tests deliberately run its quotas to the wall."""
    return api.provision_sandbox()


def test_the_fourth_endpoint_is_rejected_by_the_sandbox_endpoint_cap(
    api: LiveApi, live_config: LiveConfig, abuse_sandbox: Sandbox
) -> None:
    cap = int(abuse_sandbox.quotas["max_endpoints"])
    for index in range(cap):
        api.register_endpoint(
            abuse_sandbox, f"https://example.com/hook-{index}", [f"quota.endpoint.{index}"]
        )

    over = api.post(
        "/v1/endpoints",
        headers=abuse_sandbox.headers,
        json={"url": "https://example.com/hook-over", "subscribed_event_types": ["quota.over"]},
    )

    assert over.status_code == 403, over.text
    assert over.headers["content-type"].startswith("application/problem+json")
    assert "endpoints" in over.json()["detail"]


def test_the_event_past_the_cap_is_rejected_independently_of_the_rate_limit(
    api: LiveApi, abuse_sandbox: Sandbox
) -> None:
    cap = int(abuse_sandbox.quotas["max_events"])

    accepted = 0
    rejection: httpx.Response | None = None
    for _ in range(cap + 1):
        # Paced past the rate limiter on purpose: the count cap and the rate limit are
        # independent controls, and this test is about the count cap.
        response = api.trigger_settled(
            abuse_sandbox, event_type="quota.endpoint.0", payload={"n": accepted}
        )
        if response.status_code == 202:
            accepted += 1
            continue
        rejection = response
        break

    assert rejection is not None, f"the sandbox accepted more than its {cap}-event cap"
    assert rejection.status_code == 403, rejection.text
    assert accepted == cap
    assert "events" in rejection.json()["detail"]


def test_the_sandbox_rate_limit_answers_with_429_and_a_usable_retry_after(
    api: LiveApi, sandbox: Sandbox
) -> None:
    """A sandbox's per-second budget is tighter than a real tenant's; a burst of triggers
    must be refused with a `Retry-After` a client can actually obey."""
    responses = [api.trigger(sandbox, event_type="rate.probe") for _ in range(8)]

    limited = [r for r in responses if r.status_code == 429]
    assert limited, (
        f"no request in a burst of 8 was rate limited: {[r.status_code for r in responses]}"
    )
    retry_after = float(limited[0].headers["Retry-After"])
    assert 0 < retry_after <= 60
    assert limited[0].json()["status"] == 429


def test_sandbox_creation_is_rate_limited_per_ip(api: LiveApi) -> None:
    """The one route that hands an unauthenticated caller an identity, and therefore the
    one most worth farming. Asserted last-ish in the module because it deliberately drains
    the bucket every other sandbox in this suite draws from."""
    statuses = []
    for _ in range(SANDBOX_CREATION_PROBE_ATTEMPTS):
        response = api.post("/v1/sandbox")
        statuses.append(response.status_code)
        if response.status_code == 429:
            assert float(response.headers["Retry-After"]) > 0
            assert response.headers["content-type"].startswith("application/problem+json")
            return
    pytest.fail(f"sandbox creation was never rate limited across {statuses}")


def test_an_oversized_payload_is_rejected_with_413(api: LiveApi, sandbox: Sandbox) -> None:
    response = api.post(
        "/v1/events",
        headers={**sandbox.headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"type": "oversize.probe", "payload": {"blob": "x" * OVERSIZED_PAYLOAD_BYTES}},
    )

    assert response.status_code == 413, response.text
    assert response.headers["content-type"].startswith("application/problem+json")


def test_a_sandbox_key_stops_working_once_its_ttl_has_elapsed(
    api: LiveApi, requires_docker: None
) -> None:
    """The TTL is 60 minutes, which no test may wait for -- so the clock is skipped by
    back-dating the key's expiry in the deployed database, and the rejection is then
    observed over the real HTTP auth path, not by calling `ApiKey.is_expired()`."""
    expiring = api.provision_sandbox()
    db = dockerctl.psql()
    assert api.get("/v1/endpoints", headers=expiring.headers).status_code == 200

    db.execute(
        "UPDATE api_keys SET expires_at = now() - interval '1 minute' "
        f"WHERE tenant_id = '{expiring.tenant_id}'"
    )

    after_ttl = api.get("/v1/endpoints", headers=expiring.headers)
    assert after_ttl.status_code == 401
    assert after_ttl.json()["detail"] == "missing or invalid API key"
