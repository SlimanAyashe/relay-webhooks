import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from relay.infra.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def _migrated_schema(postgres_url: str) -> None:
    """Applies schema exclusively via `alembic upgrade head` — never
    Base.metadata.create_all — against a real, ephemeral Postgres container.

    migrations/env.py always sources its URL from Settings (same as the app
    itself), so the container URL has to flow through DATABASE_URL + a cache
    clear rather than an alembic Config override, which env.py would ignore.
    """
    os.environ["DATABASE_URL"] = postgres_url
    get_settings.cache_clear()
    try:
        alembic_cfg = Config(str(REPO_ROOT / "alembic.ini"))
        command.upgrade(alembic_cfg, "head")
    finally:
        del os.environ["DATABASE_URL"]
        get_settings.cache_clear()


@pytest_asyncio.fixture
async def db_engine(postgres_url: str, _migrated_schema: None) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(postgres_url)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session bound to a single connection's transaction, always rolled back at
    teardown — repository/service tests get a clean, isolated slate against the real,
    already-migrated schema without recreating the container per test.
    """
    async with db_engine.connect() as conn:
        await conn.begin()
        async with AsyncSession(bind=conn, expire_on_commit=False) as session:
            yield session
        await conn.rollback()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as redis_c:
        host = redis_c.get_container_host_ip()
        port = redis_c.get_exposed_port(redis_c.port)
        yield f"redis://{host}:{port}/0"


@pytest_asyncio.fixture
async def redis_client(redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_url, decode_responses=True)
    yield client
    await client.aclose()
