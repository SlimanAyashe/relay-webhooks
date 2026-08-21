import uuid
from collections.abc import Awaitable, Callable

from fastapi.testclient import TestClient

from relay.infra.settings import Settings, get_settings

AuthHeaders = Callable[[frozenset[str]], Awaitable[tuple[uuid.UUID, dict[str, str]]]]


def _override_settings(client: TestClient, **overrides: object) -> None:
    base = Settings()
    merged = base.model_copy(update=overrides)
    client.app.dependency_overrides[get_settings] = lambda: merged  # type: ignore[attr-defined]


def test_create_sandbox_returns_scoped_key_and_quotas(wired_client: TestClient) -> None:
    response = wired_client.post("/v1/sandbox")

    assert response.status_code == 201
    body = response.json()
    assert "." in body["api_key"]
    assert body["quotas"]["max_endpoints"] > 0
    assert body["quotas"]["max_events"] > 0
    assert body["expires_at"]


def test_sandbox_key_can_authenticate_against_v1_routes(wired_client: TestClient) -> None:
    sandbox = wired_client.post("/v1/sandbox").json()
    headers = {"X-API-Key": sandbox["api_key"]}

    response = wired_client.get("/v1/endpoints", headers=headers)

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_sandbox_creation_is_rate_limited_per_ip(wired_client: TestClient) -> None:
    """Testing scenario (Phase 4, backlog p4-10): per-IP rate limit on POST /v1/sandbox."""
    _override_settings(
        wired_client,
        sandbox_creation_rate_limit_burst=2,
        sandbox_creation_rate_limit_requests_per_second=0.0001,
    )

    first = wired_client.post("/v1/sandbox")
    second = wired_client.post("/v1/sandbox")
    third = wired_client.post("/v1/sandbox")

    assert first.status_code == second.status_code == 201
    assert third.status_code == 429
    assert "retry-after" in {k.lower() for k in third.headers}


def test_sandbox_endpoint_quota_rejects_the_endpoint_at_the_cap(wired_client: TestClient) -> None:
    """Testing scenario (Phase 4, backlog p4-26): a sandbox tenant registering its
    (max_endpoints + 1)-th endpoint gets 403, not the normal-tenant unlimited behavior.
    """
    _override_settings(wired_client, sandbox_max_endpoints=2)
    sandbox = wired_client.post("/v1/sandbox").json()
    headers = {"X-API-Key": sandbox["api_key"]}
    body = {"url": "https://example.com/webhook", "subscribed_event_types": ["x"]}

    first = wired_client.post("/v1/endpoints", json=body, headers=headers)
    second = wired_client.post("/v1/endpoints", json=body, headers=headers)
    third = wired_client.post("/v1/endpoints", json=body, headers=headers)

    assert first.status_code == second.status_code == 201
    assert third.status_code == 403


async def test_normal_tenant_endpoint_registration_is_never_quota_capped(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _override_settings(wired_client, sandbox_max_endpoints=1)
    _, headers = await auth_headers(frozenset({"*"}))
    body = {"url": "https://example.com/webhook", "subscribed_event_types": ["x"]}

    first = wired_client.post("/v1/endpoints", json=body, headers=headers)
    second = wired_client.post("/v1/endpoints", json=body, headers=headers)

    assert first.status_code == second.status_code == 201


def test_sandbox_event_quota_rejects_the_event_at_the_cap(wired_client: TestClient) -> None:
    """Testing scenario (Phase 4, backlog p4-26): max events, independent of the
    sandbox's tighter per-second rate limit (pinned generously here so it never fires).
    """
    _override_settings(
        wired_client,
        sandbox_max_events=2,
        sandbox_rate_limit_requests_per_second=1000.0,
        sandbox_rate_limit_burst=1000,
    )
    sandbox = wired_client.post("/v1/sandbox").json()
    headers = {"X-API-Key": sandbox["api_key"]}

    def _ingest(key: str) -> int:
        return wired_client.post(
            "/v1/events",
            json={"type": "demo.triggered", "payload": {}},
            headers={**headers, "Idempotency-Key": key},
        ).status_code

    assert _ingest("a") == 202
    assert _ingest("b") == 202
    assert _ingest("c") == 403


def test_sandbox_event_rate_limit_is_tighter_than_a_normal_tenant(wired_client: TestClient) -> None:
    _override_settings(
        wired_client, sandbox_rate_limit_burst=1, sandbox_rate_limit_requests_per_second=0.0001
    )
    sandbox = wired_client.post("/v1/sandbox").json()
    headers = {"X-API-Key": sandbox["api_key"]}

    first = wired_client.post(
        "/v1/events",
        json={"type": "demo.triggered", "payload": {}},
        headers={**headers, "Idempotency-Key": "a"},
    )
    second = wired_client.post(
        "/v1/events",
        json={"type": "demo.triggered", "payload": {}},
        headers={**headers, "Idempotency-Key": "b"},
    )

    assert first.status_code == 202
    assert second.status_code == 429
