import uuid
from collections.abc import Awaitable, Callable

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from relay.repositories.unit_of_work import UnitOfWork

AuthHeaders = Callable[[frozenset[str]], Awaitable[tuple[uuid.UUID, dict[str, str]]]]


async def _seed_dead_delivery(postgres_url: str, tenant_id: uuid.UUID) -> uuid.UUID:
    engine = create_async_engine(postgres_url)
    try:
        uow = UnitOfWork(async_sessionmaker(engine, expire_on_commit=False))
        async with uow:
            endpoint = await uow.endpoints.create(
                tenant_id=tenant_id,
                url="https://dead.example.com/webhook",
                secret="s3cr3t",
                subscribed_event_types=frozenset({"order.created"}),
            )
            event = await uow.events.create(
                tenant_id=tenant_id,
                event_type="order.created",
                payload={"n": 1},
                idempotency_key=str(uuid.uuid4()),
            )
            delivery = await uow.deliveries.create(event_id=event.id, endpoint_id=endpoint.id)
            await uow.delivery_attempts.create(
                delivery_id=delivery.id, attempt_no=1, latency_ms=5, response_status=500
            )
            await uow.deliveries.mark_dead(delivery.id)
            await uow.commit()
        return delivery.id
    finally:
        await engine.dispose()


def test_missing_api_key_returns_401(wired_client: TestClient) -> None:
    response = wired_client.get("/v1/dlq")
    assert response.status_code == 401


async def test_wrong_scope_returns_403(wired_client: TestClient, auth_headers: AuthHeaders) -> None:
    _, headers = await auth_headers(frozenset({"events:write"}))

    response = wired_client.get("/v1/dlq", headers=headers)

    assert response.status_code == 403


async def test_empty_dlq_returns_empty_list(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, headers = await auth_headers(frozenset({"*"}))

    response = wired_client.get("/v1/dlq", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None, "has_more": False}


async def test_lists_dead_delivery_with_its_attempt_history(
    wired_client: TestClient, auth_headers: AuthHeaders, postgres_url: str
) -> None:
    tenant_id, headers = await auth_headers(frozenset({"*"}))
    delivery_id = await _seed_dead_delivery(postgres_url, tenant_id)

    response = wired_client.get("/v1/dlq", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["id"] == str(delivery_id)
    assert item["state"] == "dead"
    assert len(item["attempts"]) == 1
    assert item["attempts"][0]["response_status"] == 500


async def test_dlq_is_scoped_to_tenant(
    wired_client: TestClient, auth_headers: AuthHeaders, postgres_url: str
) -> None:
    _tenant_a, headers_a = await auth_headers(frozenset({"*"}))
    tenant_b, headers_b = await auth_headers(frozenset({"*"}))
    await _seed_dead_delivery(postgres_url, tenant_b)

    response_a = wired_client.get("/v1/dlq", headers=headers_a)
    response_b = wired_client.get("/v1/dlq", headers=headers_b)

    assert response_a.json()["items"] == []
    assert len(response_b.json()["items"]) == 1


async def test_dlq_rejects_invalid_cursor(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, headers = await auth_headers(frozenset({"*"}))

    response = wired_client.get("/v1/dlq", params={"cursor": "not-a-valid-cursor"}, headers=headers)

    assert response.status_code == 422
