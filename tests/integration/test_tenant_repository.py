import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from relay.repositories.tenants.repository import TenantRepository


async def test_create_and_get_round_trip(db_session: AsyncSession) -> None:
    repo = TenantRepository(db_session)
    created = await repo.create(name="acme")

    fetched = await repo.get(created.id)

    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.name == "acme"
    assert fetched.deleted_at is None


async def test_get_missing_tenant_returns_none(db_session: AsyncSession) -> None:
    repo = TenantRepository(db_session)

    assert await repo.get(uuid.uuid4()) is None


async def test_list_returns_created_tenants(db_session: AsyncSession) -> None:
    repo = TenantRepository(db_session)
    await repo.create(name="first")
    await repo.create(name="second")

    names = {tenant.name for tenant in await repo.list()}

    assert {"first", "second"} <= names
