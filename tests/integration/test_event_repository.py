import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from relay.repositories.events.repository import EventRepository, IdempotencyKeyConflict
from relay.repositories.tenants.repository import TenantRepository


async def _make_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    tenant = await TenantRepository(db_session).create(name="acme")
    return tenant.id


async def test_create_and_get_round_trip(db_session: AsyncSession) -> None:
    tenant_id = await _make_tenant_id(db_session)
    repo = EventRepository(db_session)

    created = await repo.create(
        tenant_id=tenant_id,
        event_type="order.created",
        payload={"order_id": "1"},
        idempotency_key="idem-1",
    )
    fetched = await repo.get(created.id)

    assert fetched is not None
    assert fetched.payload == {"order_id": "1"}


async def test_get_by_idempotency_key_finds_match(db_session: AsyncSession) -> None:
    tenant_id = await _make_tenant_id(db_session)
    repo = EventRepository(db_session)
    created = await repo.create(
        tenant_id=tenant_id, event_type="t", payload={}, idempotency_key="idem-1"
    )

    found = await repo.get_by_idempotency_key(tenant_id, "idem-1")

    assert found is not None
    assert found.id == created.id


async def test_get_by_idempotency_key_no_match_returns_none(db_session: AsyncSession) -> None:
    tenant_id = await _make_tenant_id(db_session)
    repo = EventRepository(db_session)

    assert await repo.get_by_idempotency_key(tenant_id, "missing") is None


async def test_duplicate_idempotency_key_raises_typed_conflict(db_session: AsyncSession) -> None:
    tenant_id = await _make_tenant_id(db_session)
    repo = EventRepository(db_session)
    await repo.create(tenant_id=tenant_id, event_type="t", payload={}, idempotency_key="idem-1")

    with pytest.raises(IdempotencyKeyConflict):
        await repo.create(
            tenant_id=tenant_id,
            event_type="t",
            payload={"different": "body"},
            idempotency_key="idem-1",
        )

    # the failed insert's SAVEPOINT rolled back rather than aborting the whole
    # transaction — the session is still usable and only the first row exists.
    found = await repo.get_by_idempotency_key(tenant_id, "idem-1")
    assert found is not None
    assert found.payload == {}


async def test_same_idempotency_key_different_tenants_is_allowed(db_session: AsyncSession) -> None:
    tenant_a = await _make_tenant_id(db_session)
    tenant_b = await _make_tenant_id(db_session)
    repo = EventRepository(db_session)

    await repo.create(tenant_id=tenant_a, event_type="t", payload={}, idempotency_key="shared-key")
    created_b = await repo.create(
        tenant_id=tenant_b, event_type="t", payload={}, idempotency_key="shared-key"
    )

    assert created_b.tenant_id == tenant_b
