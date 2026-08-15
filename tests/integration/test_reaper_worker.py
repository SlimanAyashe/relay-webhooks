import asyncio
import uuid

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from relay.domain.deliveries import DeliveryState
from relay.infra.http_sender import OutboundHttpResult
from relay.infra.streams import DELIVERY_STREAM, DISPATCH_GROUP, enqueue_delivery, read_deliveries
from relay.repositories.unit_of_work import UnitOfWork
from relay.workers.reaper import run_once
from tests.fakes import FakeOutboundHttpSender


def _sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _seed_delivery(sessionmaker: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    uow = UnitOfWork(sessionmaker)
    async with uow:
        tenant = await uow.tenants.create(name=f"acme-{uuid.uuid4()}")
        endpoint = await uow.endpoints.create(
            tenant_id=tenant.id,
            url="https://example.com/webhook",
            secret="s3cr3t",
            subscribed_event_types=frozenset({"order.created"}),
        )
        event = await uow.events.create(
            tenant_id=tenant.id, event_type="order.created", payload={}, idempotency_key="i"
        )
        delivery = await uow.deliveries.create(event_id=event.id, endpoint_id=endpoint.id)
        await uow.commit()
    return delivery.id


async def test_run_once_reclaims_and_processes_a_message_a_dead_consumer_never_acked(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    """Testing scenario #3: a worker reads a message (XREADGROUP) and then dies before
    acking. The reaper's XAUTOCLAIM sweep reclaims it, reprocesses it through the normal
    delivery logic, and acks it -- turning "died mid-delivery" into "delivered a little
    late" instead of "silently dropped."
    """
    sessionmaker = _sessionmaker(db_engine)
    delivery_id = await _seed_delivery(sessionmaker)
    await enqueue_delivery(stream_redis, delivery_id)

    # A consumer reads the message and then never acks -- simulating a crash.
    read = await read_deliveries(stream_redis, "dead-worker", count=10, block_ms=100)
    assert len(read) == 1
    await asyncio.sleep(0.05)

    sender = FakeOutboundHttpSender([OutboundHttpResult(latency_ms=5, status_code=200)])
    reclaimed = await run_once(
        stream_redis,
        sender,
        consumer_name="reaper-1",
        min_idle_ms=10,
        uow_factory=lambda: UnitOfWork(sessionmaker),
    )

    assert reclaimed == 1
    async with UnitOfWork(sessionmaker) as check_uow:
        delivery = await check_uow.deliveries.get(delivery_id)
        assert delivery is not None
        assert delivery.state is DeliveryState.DELIVERED

    pending = await stream_redis.xpending(DELIVERY_STREAM, DISPATCH_GROUP)
    assert pending["pending"] == 0


async def test_run_once_leaves_recently_read_messages_alone(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    sessionmaker = _sessionmaker(db_engine)
    delivery_id = await _seed_delivery(sessionmaker)
    await enqueue_delivery(stream_redis, delivery_id)
    await read_deliveries(stream_redis, "still-alive-worker", count=10, block_ms=100)

    sender = FakeOutboundHttpSender([])
    reclaimed = await run_once(stream_redis, sender, consumer_name="reaper-1", min_idle_ms=60_000)

    assert reclaimed == 0
