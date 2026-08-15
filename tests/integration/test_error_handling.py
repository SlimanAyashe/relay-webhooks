import uuid
from collections.abc import Awaitable, Callable

from fastapi.testclient import TestClient

AuthHeaders = Callable[[frozenset[str]], Awaitable[tuple[uuid.UUID, dict[str, str]]]]

_PROBLEM_FIELDS = {"type", "title", "status", "detail", "instance", "trace_id"}


async def test_404_response_matches_rfc9457_shape(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, headers = await auth_headers(frozenset({"*"}))

    response = wired_client.get(f"/v1/endpoints/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body.keys() >= _PROBLEM_FIELDS
    assert body["status"] == 404
    assert body["title"] == "Not Found"


def test_401_response_matches_rfc9457_shape(wired_client: TestClient) -> None:
    response = wired_client.get("/v1/endpoints")

    assert response.status_code == 401
    body = response.json()
    assert body.keys() >= _PROBLEM_FIELDS
    assert body["status"] == 401


async def test_409_conflict_response_matches_rfc9457_shape(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, headers = await auth_headers(frozenset({"*"}))
    headers = {**headers, "Idempotency-Key": "idem-1"}
    wired_client.post(
        "/v1/events", json={"type": "order.created", "payload": {"a": 1}}, headers=headers
    )

    response = wired_client.post(
        "/v1/events", json={"type": "order.created", "payload": {"a": 2}}, headers=headers
    )

    assert response.status_code == 409
    body = response.json()
    assert body.keys() >= _PROBLEM_FIELDS
    assert body["status"] == 409


async def test_pydantic_422_uses_the_same_envelope_with_field_errors_in_detail(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, headers = await auth_headers(frozenset({"*"}))

    # subscribed_event_types is required and missing entirely.
    response = wired_client.post(
        "/v1/endpoints", json={"url": "https://example.com/hook"}, headers=headers
    )

    assert response.status_code == 422
    body = response.json()
    assert body.keys() >= _PROBLEM_FIELDS
    assert body["title"] == "Validation Error"
    assert isinstance(body["detail"], list)
    assert any("subscribed_event_types" in str(err.get("loc")) for err in body["detail"])


async def test_trace_id_propagates_from_request_header(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, headers = await auth_headers(frozenset({"*"}))
    headers = {**headers, "X-Trace-Id": "my-custom-trace-id"}

    response = wired_client.get(f"/v1/endpoints/{uuid.uuid4()}", headers=headers)

    assert response.headers["x-trace-id"] == "my-custom-trace-id"
    assert response.json()["trace_id"] == "my-custom-trace-id"


async def test_trace_id_generated_when_absent_and_matches_header_and_body(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, headers = await auth_headers(frozenset({"*"}))

    response = wired_client.get(f"/v1/endpoints/{uuid.uuid4()}", headers=headers)

    trace_id = response.headers["x-trace-id"]
    assert trace_id
    assert response.json()["trace_id"] == trace_id


def test_unmatched_route_still_returns_problem_json(wired_client: TestClient) -> None:
    response = wired_client.get("/v1/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json().keys() >= _PROBLEM_FIELDS


def test_successful_response_still_carries_trace_id_header(wired_client: TestClient) -> None:
    response = wired_client.get("/healthz")

    assert response.status_code == 200
    assert response.headers["x-trace-id"]
