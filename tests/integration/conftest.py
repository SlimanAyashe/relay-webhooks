import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from relay.infra.redis import get_redis
from relay.infra.settings import get_settings
from relay.infra.streams import DELIVERY_STREAM, DISPATCH_GROUP, ensure_consumer_group
from relay.repositories.unit_of_work import UnitOfWork, get_unit_of_work
from relay.services.api_keys.service import ApiKeyService

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


@pytest_asyncio.fixture(autouse=True)
async def _clean_globally_scoped_tables(
    postgres_url: str, _migrated_schema: None
) -> AsyncIterator[None]:
    """`outbox.claim_due()` (and, transitively, the relay/dispatcher/reaper/scheduler
    workers) query with no tenant filter -- correct production behavior, since the relay
    has to see every tenant's pending work. But the Postgres container is session-scoped and
    shared across every test file, and tests that commit real rows via UnitOfWork (rather
    than the rollback-scoped db_session fixture, e.g. anything exercising
    EventIngestService or a worker end to end) would otherwise leave permanent rows behind
    that a later test's *unscoped* claim would see too. Truncate after every test so debris
    from one test can never outlive it and leak into another's assertions.
    """
    yield
    engine = create_async_engine(postgres_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE outbox, deliveries, delivery_attempts CASCADE"))
    finally:
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


@pytest_asyncio.fixture
async def stream_redis(redis_client: Redis) -> AsyncIterator[Redis]:
    """A Redis client with a clean keyspace and the delivery consumer group already created
    -- for tests exercising real XADD/XREADGROUP/XACK/XAUTOCLAIM semantics, not a mocked
    stream. Flushes before *and* after: the container (and its keyspace) is shared for the
    whole test session, so leftover state from one test could otherwise leak into the next.
    """
    await redis_client.flushdb()
    await ensure_consumer_group(redis_client, stream=DELIVERY_STREAM, group=DISPATCH_GROUP)
    yield redis_client
    await redis_client.flushdb()


async def _setup_tenant_and_key(postgres_url: str, scopes: frozenset[str]) -> tuple[uuid.UUID, str]:
    """Uses its own engine (built directly from postgres_url, not the shared db_engine
    fixture) so its connections are bound to whichever event loop calls this, never
    accidentally shared with the TestClient portal's separate loop -- asyncpg
    connections can't cross loops.
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


@pytest.fixture
def auth_headers(
    postgres_url: str,
) -> Callable[[frozenset[str]], Awaitable[tuple[uuid.UUID, dict[str, str]]]]:
    """Factory fixture: `tenant_id, headers = await auth_headers(frozenset({"..."}))`
    creates a fresh tenant and an API key scoped to it, ready to use as request headers.
    """

    async def _make(scopes: frozenset[str] = frozenset({"*"})) -> tuple[uuid.UUID, dict[str, str]]:
        tenant_id, plaintext_key = await _setup_tenant_and_key(postgres_url, scopes)
        return tenant_id, {"X-API-Key": plaintext_key}

    return _make


@pytest_asyncio.fixture
async def wired_client(
    client: TestClient, postgres_url: str, redis_url: str, stream_redis: Redis
) -> AsyncIterator[TestClient]:
    """Its own engine too, for the same cross-loop reason as auth_headers -- this one is
    only ever used from inside the TestClient portal thread's loop, since
    dependency_overrides only calls the lambda while FastAPI is resolving a request. Redis
    gets the same treatment: the override builds a brand new client per dependency
    resolution (from `redis_url`, not by reusing the `stream_redis` fixture's own client
    object) since a Redis client holds a connection bound to the event loop that created
    it, and reusing one across the portal thread's separate loop raises exactly the kind
    of "attached to a different loop" error the DB side avoids the same way. `stream_redis`
    is still depended on here purely for its flush-before/after side effect, so routes
    hitting Redis (rate limiting, DLQ replay's re-enqueue) see a clean keyspace per test.
    """
    sessionmaker = async_sessionmaker(create_async_engine(postgres_url), expire_on_commit=False)
    client.app.dependency_overrides[get_unit_of_work] = lambda: UnitOfWork(sessionmaker)  # type: ignore[attr-defined]
    client.app.dependency_overrides[get_redis] = lambda: Redis.from_url(  # type: ignore[attr-defined]
        redis_url, decode_responses=True
    )
    yield client
    client.app.dependency_overrides.clear()  # type: ignore[attr-defined]
