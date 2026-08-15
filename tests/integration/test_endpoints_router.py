import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from relay.repositories.unit_of_work import UnitOfWork, get_unit_of_work
from relay.services.api_keys.service import ApiKeyService


async def _setup_tenant_and_key(postgres_url: str, scopes: frozenset[str]) -> tuple[uuid.UUID, str]:
    """Uses its own engine (built directly from postgres_url, not the shared db_engine
    fixture) so its connections are bound to this async test's event loop, never the
    TestClient portal's separate loop -- asyncpg connections can't cross loops.
    """
    engine = create_async_engine(postgres_url)
    try:
        uow = UnitOfWork(async_sessionmaker(engine, expire_on_commit=False))
        async with uow:
            tenant = await uow.tenants.create(name=f"acme-{uuid.uuid4()}")
            await uow.commit()
        _, plaintext_key = await ApiKeyService(uow).issue(tenant.id, scopes)
        return tenant.id, plaintext_key
    finally:
        await engine.dispose()


async def _auth_headers(
    postgres_url: str, scopes: frozenset[str]
) -> tuple[uuid.UUID, dict[str, str]]:
    tenant_id, plaintext_key = await _setup_tenant_and_key(postgres_url, scopes)
    return tenant_id, {"X-API-Key": plaintext_key}


@pytest.fixture
def wired_client(client: TestClient, postgres_url: str) -> Iterator[TestClient]:
    """Its own engine too, for the same cross-loop reason -- this one is only ever used
    from inside the TestClient portal thread's loop, since dependency_overrides only
    calls the lambda while FastAPI is resolving a request.
    """
    sessionmaker = async_sessionmaker(create_async_engine(postgres_url), expire_on_commit=False)
    client.app.dependency_overrides[get_unit_of_work] = lambda: UnitOfWork(sessionmaker)  # type: ignore[attr-defined]
    yield client
    client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


async def test_create_get_list_endpoint(wired_client: TestClient, postgres_url: str) -> None:
    _, headers = await _auth_headers(postgres_url, frozenset({"*"}))

    create_response = wired_client.post(
        "/v1/endpoints",
        json={"url": "https://example.com/hook", "subscribed_event_types": ["order.created"]},
        headers=headers,
    )
    assert create_response.status_code == 201
    body = create_response.json()
    assert body["url"] == "https://example.com/hook"
    assert "secret" in body
    endpoint_id = body["id"]

    get_response = wired_client.get(f"/v1/endpoints/{endpoint_id}", headers=headers)
    assert get_response.status_code == 200
    assert "secret" not in get_response.json()

    list_response = wired_client.get("/v1/endpoints", headers=headers)
    assert list_response.status_code == 200
    list_body = list_response.json()
    assert [e["id"] for e in list_body["items"]] == [endpoint_id]
    assert list_body["has_more"] is False
    assert list_body["next_cursor"] is None


async def test_create_endpoint_rejects_non_https_url(
    wired_client: TestClient, postgres_url: str
) -> None:
    _, headers = await _auth_headers(postgres_url, frozenset({"*"}))

    response = wired_client.post(
        "/v1/endpoints",
        json={"url": "http://example.com/hook", "subscribed_event_types": ["order.created"]},
        headers=headers,
    )

    assert response.status_code == 422


def test_missing_api_key_returns_401(wired_client: TestClient) -> None:
    response = wired_client.get("/v1/endpoints")
    assert response.status_code == 401


async def test_wrong_scope_returns_403(wired_client: TestClient, postgres_url: str) -> None:
    _, headers = await _auth_headers(postgres_url, frozenset({"events:read"}))

    response = wired_client.get("/v1/endpoints", headers=headers)

    assert response.status_code == 403


async def test_get_missing_endpoint_returns_404(
    wired_client: TestClient, postgres_url: str
) -> None:
    _, headers = await _auth_headers(postgres_url, frozenset({"*"}))

    response = wired_client.get(f"/v1/endpoints/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404


async def test_cross_tenant_get_returns_404(wired_client: TestClient, postgres_url: str) -> None:
    _, owner_headers = await _auth_headers(postgres_url, frozenset({"*"}))
    _, other_headers = await _auth_headers(postgres_url, frozenset({"*"}))
    create_response = wired_client.post(
        "/v1/endpoints",
        json={"url": "https://example.com/hook", "subscribed_event_types": ["x"]},
        headers=owner_headers,
    )
    endpoint_id = create_response.json()["id"]

    response = wired_client.get(f"/v1/endpoints/{endpoint_id}", headers=other_headers)

    assert response.status_code == 404


async def test_update_and_delete_endpoint(wired_client: TestClient, postgres_url: str) -> None:
    _, headers = await _auth_headers(postgres_url, frozenset({"*"}))
    create_response = wired_client.post(
        "/v1/endpoints",
        json={"url": "https://example.com/hook", "subscribed_event_types": ["x"]},
        headers=headers,
    )
    endpoint_id = create_response.json()["id"]

    patch_response = wired_client.patch(
        f"/v1/endpoints/{endpoint_id}", json={"status": "disabled"}, headers=headers
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["status"] == "disabled"

    delete_response = wired_client.delete(f"/v1/endpoints/{endpoint_id}", headers=headers)
    assert delete_response.status_code == 204

    get_response = wired_client.get(f"/v1/endpoints/{endpoint_id}", headers=headers)
    assert get_response.status_code == 404


async def test_list_endpoints_paginates_with_no_skips_or_duplicates(
    wired_client: TestClient, postgres_url: str
) -> None:
    _, headers = await _auth_headers(postgres_url, frozenset({"*"}))
    created_ids = set()
    for i in range(5):
        response = wired_client.post(
            "/v1/endpoints",
            json={"url": f"https://example.com/hook-{i}", "subscribed_event_types": ["x"]},
            headers=headers,
        )
        created_ids.add(response.json()["id"])

    seen_ids: list[str] = []
    cursor: str | None = None
    while True:
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        response = wired_client.get("/v1/endpoints", params=params, headers=headers)
        assert response.status_code == 200
        body = response.json()
        seen_ids.extend(item["id"] for item in body["items"])
        if not body["has_more"]:
            assert body["next_cursor"] is None
            break
        assert body["next_cursor"] is not None
        cursor = body["next_cursor"]

    assert len(seen_ids) == len(set(seen_ids)) == len(created_ids)
    assert set(seen_ids) == created_ids


async def test_list_endpoints_rejects_invalid_cursor(
    wired_client: TestClient, postgres_url: str
) -> None:
    _, headers = await _auth_headers(postgres_url, frozenset({"*"}))

    response = wired_client.get(
        "/v1/endpoints", params={"cursor": "not-a-valid-cursor"}, headers=headers
    )

    assert response.status_code == 422
