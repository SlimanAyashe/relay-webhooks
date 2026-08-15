import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from relay.domain.endpoints import EndpointStatus
from relay.repositories.unit_of_work import UnitOfWork
from relay.services.endpoints.service import EndpointService


def _service(db_engine: AsyncEngine) -> tuple[EndpointService, UnitOfWork]:
    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)
    uow = UnitOfWork(sessionmaker)
    return EndpointService(uow), uow


async def _make_tenant_id(uow: UnitOfWork) -> uuid.UUID:
    async with uow:
        tenant = await uow.tenants.create(name=f"acme-{uuid.uuid4()}")
        await uow.commit()
    return tenant.id


async def test_register_creates_endpoint_with_generated_secret(db_engine: AsyncEngine) -> None:
    service, uow = _service(db_engine)
    tenant_id = await _make_tenant_id(uow)

    endpoint = await service.register(
        tenant_id, "https://example.com/webhook", frozenset({"order.created"})
    )

    assert endpoint.url == "https://example.com/webhook"
    assert len(endpoint.secret) > 0
    assert endpoint.status is EndpointStatus.ACTIVE


async def test_register_rejects_non_https_url(db_engine: AsyncEngine) -> None:
    service, uow = _service(db_engine)
    tenant_id = await _make_tenant_id(uow)

    with pytest.raises(ValueError, match="https"):
        await service.register(tenant_id, "http://example.com/webhook", frozenset({"a"}))


async def test_register_rejects_empty_event_types(db_engine: AsyncEngine) -> None:
    service, uow = _service(db_engine)
    tenant_id = await _make_tenant_id(uow)

    with pytest.raises(ValueError, match="empty"):
        await service.register(tenant_id, "https://example.com/webhook", frozenset())


async def test_list_scopes_to_tenant(db_engine: AsyncEngine) -> None:
    service, uow = _service(db_engine)
    tenant_a = await _make_tenant_id(uow)
    tenant_b = await _make_tenant_id(uow)
    await service.register(tenant_a, "https://a.example.com", frozenset({"x"}))
    await service.register(tenant_b, "https://b.example.com", frozenset({"x"}))

    page = await service.list(tenant_a)

    assert [e.url for e in page.items] == ["https://a.example.com"]


async def test_update_wrong_tenant_raises_lookup_error(db_engine: AsyncEngine) -> None:
    service, uow = _service(db_engine)
    tenant_id = await _make_tenant_id(uow)
    other_tenant_id = await _make_tenant_id(uow)
    endpoint = await service.register(tenant_id, "https://example.com", frozenset({"x"}))

    with pytest.raises(LookupError):
        await service.update(endpoint.id, other_tenant_id, status=EndpointStatus.DISABLED)


async def test_update_changes_status(db_engine: AsyncEngine) -> None:
    service, uow = _service(db_engine)
    tenant_id = await _make_tenant_id(uow)
    endpoint = await service.register(tenant_id, "https://example.com", frozenset({"x"}))

    updated = await service.update(endpoint.id, tenant_id, status=EndpointStatus.DISABLED)

    assert updated.status is EndpointStatus.DISABLED
    assert updated.url == "https://example.com"


async def test_delete_wrong_tenant_raises_lookup_error_and_does_not_delete(
    db_engine: AsyncEngine,
) -> None:
    service, uow = _service(db_engine)
    tenant_id = await _make_tenant_id(uow)
    other_tenant_id = await _make_tenant_id(uow)
    endpoint = await service.register(tenant_id, "https://example.com", frozenset({"x"}))

    with pytest.raises(LookupError):
        await service.delete(endpoint.id, other_tenant_id)

    async with uow:
        assert await uow.endpoints.get(endpoint.id) is not None


async def test_delete_removes_endpoint(db_engine: AsyncEngine) -> None:
    service, uow = _service(db_engine)
    tenant_id = await _make_tenant_id(uow)
    endpoint = await service.register(tenant_id, "https://example.com", frozenset({"x"}))

    await service.delete(endpoint.id, tenant_id)

    async with uow:
        assert await uow.endpoints.get(endpoint.id) is None
