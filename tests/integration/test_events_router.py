import uuid
from collections.abc import Awaitable, Callable

from fastapi.testclient import TestClient

from relay.infra.settings import Settings, get_settings

AuthHeaders = Callable[[frozenset[str]], Awaitable[tuple[uuid.UUID, dict[str, str]]]]


async def test_ingest_event_returns_202_with_location_header(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, headers = await auth_headers(frozenset({"*"}))
    headers = {**headers, "Idempotency-Key": "idem-1"}

    response = wired_client.post(
        "/v1/events",
        json={"type": "order.created", "payload": {"order_id": "1"}},
        headers=headers,
    )

    assert response.status_code == 202
    body = response.json()
    assert response.headers["location"] == f"/v1/events/{body['id']}"
    assert body["type"] == "order.created"
    assert body["idempotency_key"] == "idem-1"


async def test_ingest_missing_idempotency_key_returns_422(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, headers = await auth_headers(frozenset({"*"}))

    response = wired_client.post(
        "/v1/events", json={"type": "order.created", "payload": {}}, headers=headers
    )

    assert response.status_code == 422


async def test_ingest_duplicate_key_identical_body_returns_same_event(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, headers = await auth_headers(frozenset({"*"}))
    headers = {**headers, "Idempotency-Key": "idem-1"}
    body = {"type": "order.created", "payload": {"order_id": "1"}}

    first = wired_client.post("/v1/events", json=body, headers=headers)
    second = wired_client.post("/v1/events", json=body, headers=headers)

    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]


async def test_ingest_duplicate_key_differing_body_returns_409(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, headers = await auth_headers(frozenset({"*"}))
    headers = {**headers, "Idempotency-Key": "idem-1"}
    wired_client.post(
        "/v1/events",
        json={"type": "order.created", "payload": {"order_id": "1"}},
        headers=headers,
    )

    response = wired_client.post(
        "/v1/events",
        json={"type": "order.created", "payload": {"order_id": "DIFFERENT"}},
        headers=headers,
    )

    assert response.status_code == 409


def test_missing_api_key_returns_401(wired_client: TestClient) -> None:
    response = wired_client.post(
        "/v1/events",
        json={"type": "order.created", "payload": {}},
        headers={"Idempotency-Key": "idem-1"},
    )
    assert response.status_code == 401


async def test_wrong_scope_returns_403(wired_client: TestClient, auth_headers: AuthHeaders) -> None:
    _, headers = await auth_headers(frozenset({"endpoints:read"}))
    headers = {**headers, "Idempotency-Key": "idem-1"}

    response = wired_client.post(
        "/v1/events", json={"type": "order.created", "payload": {}}, headers=headers
    )

    assert response.status_code == 403


async def test_oversized_payload_returns_413(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    wired_client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined]
        event_payload_max_bytes=64
    )
    _, headers = await auth_headers(frozenset({"*"}))
    headers = {**headers, "Idempotency-Key": "idem-oversized"}

    response = wired_client.post(
        "/v1/events",
        json={"type": "order.created", "payload": {"blob": "x" * 500}},
        headers=headers,
    )

    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"


def _pin_small_rate_limit_budget(wired_client: TestClient, *, burst: int) -> None:
    """Overrides Settings with a tiny burst and a near-zero refill rate, so the test
    doesn't race real wall-clock token refill against however long `burst` HTTP round
    trips (each a full event-ingest transaction) happen to take in this environment.
    """
    wired_client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined]
        rate_limit_burst=burst, rate_limit_requests_per_second=0.001
    )


async def test_exceeding_the_rate_limit_returns_429_with_retry_after(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    """Testing scenario from the backlog: a tenant exceeding its configured rate gets 429
    + Retry-After, and (below) a different tenant's budget is unaffected.
    """
    burst = 3
    _pin_small_rate_limit_budget(wired_client, burst=burst)
    _, headers = await auth_headers(frozenset({"*"}))

    for i in range(burst):
        response = wired_client.post(
            "/v1/events",
            json={"type": "order.created", "payload": {"i": i}},
            headers={**headers, "Idempotency-Key": f"idem-{i}"},
        )
        assert response.status_code == 202, f"request {i} unexpectedly throttled"

    throttled = wired_client.post(
        "/v1/events",
        json={"type": "order.created", "payload": {"i": "over-budget"}},
        headers={**headers, "Idempotency-Key": "idem-over-budget"},
    )

    assert throttled.status_code == 429
    assert "retry-after" in {k.lower() for k in throttled.headers}
    assert int(throttled.headers["retry-after"]) >= 1
    assert throttled.headers["content-type"] == "application/problem+json"


async def test_rate_limit_is_isolated_per_tenant(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    burst = 3
    _pin_small_rate_limit_budget(wired_client, burst=burst)
    _, exhausted_headers = await auth_headers(frozenset({"*"}))
    _, other_headers = await auth_headers(frozenset({"*"}))

    for i in range(burst):
        wired_client.post(
            "/v1/events",
            json={"type": "order.created", "payload": {}},
            headers={**exhausted_headers, "Idempotency-Key": f"idem-{i}"},
        )
    throttled = wired_client.post(
        "/v1/events",
        json={"type": "order.created", "payload": {}},
        headers={**exhausted_headers, "Idempotency-Key": "idem-over-budget"},
    )
    assert throttled.status_code == 429

    # A different tenant's budget was never touched.
    unaffected = wired_client.post(
        "/v1/events",
        json={"type": "order.created", "payload": {}},
        headers={**other_headers, "Idempotency-Key": "idem-1"},
    )
    assert unaffected.status_code == 202
