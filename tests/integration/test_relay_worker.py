import uuid

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from relay.infra.streams import read_deliveries
from relay.repositories.unit_of_work import UnitOfWork
from relay.services.events.service import EventIngestService
from relay.workers.relay import run_once


def _sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _seed(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    subscribed_event_types: frozenset[str],
    event_type: str = "order.created",
) -> tuple[UnitOfWork, uuid.UUID, uuid.UUID]:
    """Creates a tenant + one active endpoint + one event with a pending outbox row,
    committed as separate transactions the way real usage would (endpoint registration and
    event ingest happen in different requests). Returns a fresh UnitOfWork ready for the
    relay worker to use, plus the endpoint and event ids for assertions.
    """
    uow = UnitOfWork(sessionmaker)
    async with uow:
        tenant = await uow.tenants.create(name=f"acme-{uuid.uuid4()}")
        endpoint = await uow.endpoints.create(
            tenant_id=tenant.id,
            url="https://example.com/webhook",
            secret="s3cr3t",
            subscribed_event_types=subscribed_event_types,
        )
        event = await uow.events.create(
            tenant_id=tenant.id, event_type=event_type, payload={}, idempotency_key="idem-1"
        )
        await uow.outbox.create(event_id=event.id)
        await uow.commit()
    return UnitOfWork(sessionmaker), endpoint.id, event.id


async def test_run_once_fans_out_to_a_subscribed_endpoint(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    sessionmaker = _sessionmaker(db_engine)
    uow, endpoint_id, event_id = await _seed(
        sessionmaker, subscribed_event_types=frozenset({"order.created"})
    )

    processed = await run_once(uow, stream_redis, batch_size=10)

    assert processed == 1
    messages = await read_deliveries(stream_redis, "test-consumer", count=10, block_ms=100)
    assert len(messages) == 1

    check_uow = UnitOfWork(sessionmaker)
    async with check_uow:
        delivery = await check_uow.deliveries.get(messages[0].delivery_id)
        assert delivery is not None
        assert delivery.endpoint_id == endpoint_id
        assert delivery.event_id == event_id


async def test_run_once_skips_endpoints_not_subscribed_to_the_event_type(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    sessionmaker = _sessionmaker(db_engine)
    uow, _endpoint_id, _event_id = await _seed(
        sessionmaker, subscribed_event_types=frozenset({"order.deleted"})
    )

    processed = await run_once(uow, stream_redis, batch_size=10)

    assert processed == 1  # the outbox row is still claimed and marked processed...
    messages = await read_deliveries(stream_redis, "test-consumer", count=10, block_ms=100)
    assert messages == []  # ...but nothing was fanned out, since no endpoint matched


async def test_run_once_returns_zero_when_nothing_is_pending(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    sessionmaker = _sessionmaker(db_engine)

    processed = await run_once(UnitOfWork(sessionmaker), stream_redis, batch_size=10)

    assert processed == 0


async def test_outbox_row_committed_before_a_relay_run_is_recovered_on_the_next_run(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    """Testing scenario #1: the event-ingest transaction commits the event and its outbox
    row durably; if the relay hasn't gotten to it yet (equivalent to a relay process that
    crashed or hadn't started), the row is simply still `pending` in Postgres and the next
    relay run (a fresh process, or the same one on its next tick) picks it up -- no special
    recovery path is needed because there was never anything to recover, only to notice.
    """
    sessionmaker = _sessionmaker(db_engine)
    # The row is committed here and nothing has consumed it yet -- standing in for "the
    # relay process that was supposed to publish this died before it got a chance to."
    uow, endpoint_id, event_id = await _seed(
        sessionmaker, subscribed_event_types=frozenset({"order.created"})
    )

    # A fresh relay run (simulating a restart) still finds and fans out the row.
    processed = await run_once(uow, stream_redis, batch_size=10)

    assert processed == 1
    messages = await read_deliveries(stream_redis, "test-consumer", count=10, block_ms=100)
    assert len(messages) == 1

    check_uow = UnitOfWork(sessionmaker)
    async with check_uow:
        delivery = await check_uow.deliveries.get(messages[0].delivery_id)
        assert delivery is not None
        assert delivery.endpoint_id == endpoint_id
        assert delivery.event_id == event_id

    # And it's not claimable again -- the outbox row was marked processed, so a second
    # relay run (or a second replica) finds nothing left to do.
    second_processed = await run_once(UnitOfWork(sessionmaker), stream_redis, batch_size=10)
    assert second_processed == 0


async def test_duplicate_ingest_fans_out_exactly_one_delivery(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    """Testing scenario #5, all the way through the fan-out: the same Idempotency-Key with
    an identical body must produce one event row *and* one delivery -- "one event row" alone
    would still allow a second outbox row to double-deliver it, which is the version of this
    bug a receiver would actually notice.
    """
    sessionmaker = _sessionmaker(db_engine)
    setup = UnitOfWork(sessionmaker)
    async with setup:
        tenant = await setup.tenants.create(name=f"acme-{uuid.uuid4()}")
        await setup.endpoints.create(
            tenant_id=tenant.id,
            url="https://example.com/webhook",
            secret="s3cr3t",
            subscribed_event_types=frozenset({"order.created"}),
        )
        await setup.commit()

    service = EventIngestService(UnitOfWork(sessionmaker))
    first = await service.ingest(tenant.id, "order.created", {"order_id": "1"}, "idem-dup")
    second = await service.ingest(tenant.id, "order.created", {"order_id": "1"}, "idem-dup")
    assert second.id == first.id

    processed = await run_once(UnitOfWork(sessionmaker), stream_redis, batch_size=10)

    assert processed == 1
    messages = await read_deliveries(stream_redis, "test-consumer", count=10, block_ms=100)
    assert len(messages) == 1
    async with sessionmaker() as session:
        delivery_count = await session.execute(
            text("SELECT COUNT(*) FROM deliveries WHERE event_id = :event_id"),
            {"event_id": str(first.id)},
        )
        assert delivery_count.scalar_one() == 1
