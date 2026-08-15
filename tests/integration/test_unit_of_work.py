import uuid

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from relay.repositories.unit_of_work import UnitOfWork


async def test_commit_persists_across_a_fresh_unit_of_work(db_engine: AsyncEngine) -> None:
    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)
    name = f"acme-{uuid.uuid4()}"

    async with UnitOfWork(sessionmaker) as uow:
        created = await uow.tenants.create(name=name)
        await uow.commit()

    async with UnitOfWork(sessionmaker) as uow:
        fetched = await uow.tenants.get(created.id)

    assert fetched is not None
    assert fetched.name == name


async def test_exception_inside_block_rolls_back(db_engine: AsyncEngine) -> None:
    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)
    name = f"acme-{uuid.uuid4()}"
    created_id: uuid.UUID | None = None

    class _Boom(Exception):
        pass

    try:
        async with UnitOfWork(sessionmaker) as uow:
            created = await uow.tenants.create(name=name)
            created_id = created.id
            raise _Boom
    except _Boom:
        pass

    assert created_id is not None
    async with UnitOfWork(sessionmaker) as uow:
        assert await uow.tenants.get(created_id) is None


async def test_forgetting_to_commit_also_rolls_back(db_engine: AsyncEngine) -> None:
    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)
    name = f"acme-{uuid.uuid4()}"

    async with UnitOfWork(sessionmaker) as uow:
        created = await uow.tenants.create(name=name)
        # deliberately no uow.commit() here

    async with UnitOfWork(sessionmaker) as uow:
        assert await uow.tenants.get(created.id) is None
