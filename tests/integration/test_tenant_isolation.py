import uuid
from collections.abc import Awaitable, Callable

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from relay.repositories.unit_of_work import UnitOfWork

AuthHeaders = Callable[[frozenset[str]], Awaitable[tuple[uuid.UUID, dict[str, str]]]]


def test_missing_api_key_returns_401(wired_client: TestClient) -> None:
    response = wired_client.get("/v1/endpoints")
    assert response.status_code == 401


def test_malformed_api_key_returns_401(wired_client: TestClient) -> None:
    response = wired_client.get(
        "/v1/endpoints", headers={"X-API-Key": "no-separator-in-this-value"}
    )
    assert response.status_code == 401


def test_unknown_api_key_returns_401(wired_client: TestClient) -> None:
    response = wired_client.get(
        "/v1/endpoints", headers={"X-API-Key": "unknownprefix.unknownsecret"}
    )
    assert response.status_code == 401


async def test_revoked_api_key_returns_401(
    wired_client: TestClient, auth_headers: AuthHeaders, postgres_url: str
) -> None:
    tenant_id, headers = await auth_headers(frozenset({"*"}))
    engine = create_async_engine(postgres_url)
    try:
        uow = UnitOfWork(async_sessionmaker(engine, expire_on_commit=False))
        async with uow:
            keys = await uow.api_keys.list(tenant_id)
            await uow.api_keys.revoke(keys[0].id)
            await uow.commit()
    finally:
        await engine.dispose()

    response = wired_client.get("/v1/endpoints", headers=headers)

    assert response.status_code == 401


async def test_tenant_a_key_cannot_read_or_modify_tenant_b_endpoint(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    """Testing scenario #12: a valid key from tenant A cannot read or modify tenant B's
    resources. Covers endpoints (events has no read/modify HTTP surface in Phase 1 --
    ingestion always scopes to the authenticated tenant, and cross-tenant idempotency-key
    independence is proven at the repository layer in test_event_repository.py).
    """
    _, tenant_a_headers = await auth_headers(frozenset({"*"}))
    _, tenant_b_headers = await auth_headers(frozenset({"*"}))
    create_response = wired_client.post(
        "/v1/endpoints",
        json={"url": "https://example.com/hook", "subscribed_event_types": ["x"]},
        headers=tenant_b_headers,
    )
    endpoint_id = create_response.json()["id"]

    get_response = wired_client.get(f"/v1/endpoints/{endpoint_id}", headers=tenant_a_headers)
    assert get_response.status_code == 404

    patch_response = wired_client.patch(
        f"/v1/endpoints/{endpoint_id}", json={"status": "disabled"}, headers=tenant_a_headers
    )
    assert patch_response.status_code == 404

    delete_response = wired_client.delete(f"/v1/endpoints/{endpoint_id}", headers=tenant_a_headers)
    assert delete_response.status_code == 404

    # confirm tenant B's endpoint was untouched by tenant A's failed attempts
    confirm_response = wired_client.get(f"/v1/endpoints/{endpoint_id}", headers=tenant_b_headers)
    assert confirm_response.status_code == 200
    assert confirm_response.json()["status"] == "active"
