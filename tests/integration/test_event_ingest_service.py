import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from relay.repositories.unit_of_work import UnitOfWork
from relay.services.events.service import DifferingBodyConflict, EventIngestService


def _sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


def _service(db_engine: AsyncEngine) -> tuple[EventIngestService, UnitOfWork]:
    uow = UnitOfWork(_sessionmaker(db_engine))
    return EventIngestService(uow), uow


async def _make_tenant_id(uow: UnitOfWork) -> uuid.UUID:
    async with uow:
        tenant = await uow.tenants.create(name=f"acme-{uuid.uuid4()}")
        await uow.commit()
    return tenant.id


async def _event_row_count(
    db_engine: AsyncEngine, tenant_id: uuid.UUID, idempotency_key: str
) -> int:
    """A raw count query -- independent of the repository/service under test -- so a bug
    in EventIngestService can't also hide its own evidence.
    """
    async with _sessionmaker(db_engine)() as session:
        result = await session.execute(
            text(
                "SELECT COUNT(*) FROM events "
                "WHERE tenant_id = :tenant_id AND idempotency_key = :idempotency_key"
            ),
            {"tenant_id": str(tenant_id), "idempotency_key": idempotency_key},
        )
        return result.scalar_one()


async def test_ingest_creates_new_event(db_engine: AsyncEngine) -> None:
    service, uow = _service(db_engine)
    tenant_id = await _make_tenant_id(uow)

    event = await service.ingest(tenant_id, "order.created", {"order_id": "1"}, "idem-1")

    assert event.type == "order.created"
    assert event.payload == {"order_id": "1"}


async def test_duplicate_key_identical_body_returns_original_no_new_row(
    db_engine: AsyncEngine,
) -> None:
    service, uow = _service(db_engine)
    tenant_id = await _make_tenant_id(uow)

    first = await service.ingest(tenant_id, "order.created", {"order_id": "1"}, "idem-1")
    second = await service.ingest(tenant_id, "order.created", {"order_id": "1"}, "idem-1")

    assert second.id == first.id
    assert await _event_row_count(db_engine, tenant_id, "idem-1") == 1


async def test_duplicate_key_differing_body_raises_conflict_no_new_row(
    db_engine: AsyncEngine,
) -> None:
    service, uow = _service(db_engine)
    tenant_id = await _make_tenant_id(uow)
    await service.ingest(tenant_id, "order.created", {"order_id": "1"}, "idem-1")

    with pytest.raises(DifferingBodyConflict):
        await service.ingest(tenant_id, "order.created", {"order_id": "DIFFERENT"}, "idem-1")

    assert await _event_row_count(db_engine, tenant_id, "idem-1") == 1


async def test_duplicate_key_differing_type_raises_conflict(db_engine: AsyncEngine) -> None:
    service, uow = _service(db_engine)
    tenant_id = await _make_tenant_id(uow)
    await service.ingest(tenant_id, "order.created", {"order_id": "1"}, "idem-1")

    with pytest.raises(DifferingBodyConflict):
        await service.ingest(tenant_id, "order.deleted", {"order_id": "1"}, "idem-1")
