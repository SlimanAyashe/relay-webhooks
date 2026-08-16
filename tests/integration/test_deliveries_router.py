import uuid
from collections.abc import Awaitable, Callable

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from relay.repositories.unit_of_work import UnitOfWork

AuthHeaders = Callable[[frozenset[str]], Awaitable[tuple[uuid.UUID, dict[str, str]]]]


async def _seed_delivery(postgres_url: str, tenant_id: uuid.UUID, *, dead: bool) -> uuid.UUID:
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
            if dead:
                await uow.delivery_attempts.create(
                    delivery_id=delivery.id, attempt_no=1, latency_ms=5, response_status=500
                )
                await uow.deliveries.mark_dead(delivery.id)
            await uow.commit()
        return delivery.id
    finally:
        await engine.dispose()


def test_missing_api_key_returns_401(wired_client: TestClient) -> None:
    response = wired_client.post(f"/v1/deliveries/{uuid.uuid4()}/replay")
    assert response.status_code == 401


async def test_wrong_scope_returns_403(wired_client: TestClient, auth_headers: AuthHeaders) -> None:
    _, headers = await auth_headers(frozenset({"events:write"}))

    response = wired_client.post(f"/v1/deliveries/{uuid.uuid4()}/replay", headers=headers)

    assert response.status_code == 403


async def test_replay_unknown_delivery_returns_404(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, headers = await auth_headers(frozenset({"*"}))

    response = wired_client.post(f"/v1/deliveries/{uuid.uuid4()}/replay", headers=headers)

    assert response.status_code == 404


async def test_replay_cross_tenant_delivery_returns_404(
    wired_client: TestClient, auth_headers: AuthHeaders, postgres_url: str
) -> None:
    """A tenant A key must not be able to tell 'doesn't exist' apart from 'belongs to
    tenant B', same isolation rule as every other resource.
    """
    tenant_a, _headers_a = await auth_headers(frozenset({"*"}))
    _, headers_b = await auth_headers(frozenset({"*"}))
    delivery_id = await _seed_delivery(postgres_url, tenant_a, dead=True)

    response = wired_client.post(f"/v1/deliveries/{delivery_id}/replay", headers=headers_b)

    assert response.status_code == 404


async def test_replay_non_dead_delivery_returns_409(
    wired_client: TestClient, auth_headers: AuthHeaders, postgres_url: str
) -> None:
    tenant_id, headers = await auth_headers(frozenset({"*"}))
    delivery_id = await _seed_delivery(postgres_url, tenant_id, dead=False)

    response = wired_client.post(f"/v1/deliveries/{delivery_id}/replay", headers=headers)

    assert response.status_code == 409


async def test_replay_dead_delivery_resets_state_and_returns_202(
    wired_client: TestClient, auth_headers: AuthHeaders, postgres_url: str
) -> None:
    tenant_id, headers = await auth_headers(frozenset({"*"}))
    delivery_id = await _seed_delivery(postgres_url, tenant_id, dead=True)

    response = wired_client.post(f"/v1/deliveries/{delivery_id}/replay", headers=headers)

    assert response.status_code == 202
    assert response.headers["location"] == f"/v1/deliveries/{delivery_id}"
    body = response.json()
    assert body["id"] == str(delivery_id)
    assert body["state"] == "pending"
    assert body["attempt_count"] == 0
