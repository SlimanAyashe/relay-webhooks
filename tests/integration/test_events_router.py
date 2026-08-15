import uuid
from collections.abc import Awaitable, Callable

from fastapi.testclient import TestClient

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
