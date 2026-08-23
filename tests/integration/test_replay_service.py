import uuid

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from relay.domain.deliveries import DeliveryState
from relay.domain.errors import NotFoundError
from relay.infra.http_sender import OutboundHttpResult
from relay.infra.streams import read_deliveries
from relay.repositories.unit_of_work import UnitOfWork
from relay.services.deliveries.replay_service import DeliveryNotDeadError, ReplayService
from relay.workers.dispatcher import process_delivery_message
from tests.fakes import FakeOutboundHttpSender


def _sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _seed_dead_delivery(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID]:
    uow = UnitOfWork(sessionmaker)
    async with uow:
        tenant = await uow.tenants.create(name=f"acme-{uuid.uuid4()}")
        endpoint = await uow.endpoints.create(
            tenant_id=tenant.id,
            url="https://dead.example.com/webhook",
            secret="s3cr3t",
            subscribed_event_types=frozenset({"order.created"}),
        )
        event = await uow.events.create(
            tenant_id=tenant.id, event_type="order.created", payload={"n": 1}, idempotency_key="i"
        )
        delivery = await uow.deliveries.create(event_id=event.id, endpoint_id=endpoint.id)
        # Two exhausted attempts, then dead -- the "original chain" that must survive replay.
        await uow.delivery_attempts.create(
            delivery_id=delivery.id, attempt_no=1, latency_ms=5, response_status=500
        )
        await uow.delivery_attempts.create(
            delivery_id=delivery.id, attempt_no=2, latency_ms=5, response_status=500
        )
        await uow.deliveries.mark_dead(delivery.id)
        await uow.commit()
    return delivery.id, tenant.id


async def test_replay_starts_a_fresh_chain_and_publishes_to_the_stream(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    sessionmaker = _sessionmaker(db_engine)
    delivery_id, tenant_id = await _seed_dead_delivery(sessionmaker)
    service = ReplayService(UnitOfWork(sessionmaker), stream_redis)

    replayed = await service.replay(delivery_id, tenant_id)

    assert replayed.state is DeliveryState.PENDING
    assert replayed.attempt_count == 0
    assert replayed.next_retry_at is None

    read = await read_deliveries(stream_redis, "replay-test-consumer", count=10, block_ms=100)
    assert [message.delivery_id for message in read] == [delivery_id]


async def test_replay_preserves_original_attempt_history_while_new_attempts_go_forward(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    """Testing scenario from the plan's end-to-end walkthrough: replay creates new
    delivery_attempts going forward while the original (exhausted) attempt rows remain
    intact and queryable.
    """
    sessionmaker = _sessionmaker(db_engine)
    delivery_id, tenant_id = await _seed_dead_delivery(sessionmaker)
    service = ReplayService(UnitOfWork(sessionmaker), stream_redis)

    await service.replay(delivery_id, tenant_id)

    async with UnitOfWork(sessionmaker) as check_uow:
        attempts_after_replay = await check_uow.delivery_attempts.list_for_delivery(delivery_id)
    assert len(attempts_after_replay) == 2
    assert [a.response_status for a in attempts_after_replay] == [500, 500]

    # A fresh attempt after replay succeeds and is appended, not merged into the old chain.
    sender = FakeOutboundHttpSender([OutboundHttpResult(latency_ms=5, status_code=200)])
    await process_delivery_message(
        lambda: UnitOfWork(sessionmaker), sender, stream_redis, delivery_id
    )

    async with UnitOfWork(sessionmaker) as check_uow:
        delivery = await check_uow.deliveries.get(delivery_id)
        all_attempts = await check_uow.delivery_attempts.list_for_delivery(delivery_id)

    assert delivery is not None
    assert delivery.state is DeliveryState.DELIVERED
    assert len(all_attempts) == 3  # the two exhausted rows plus the new post-replay one
    assert [a.response_status for a in all_attempts] == [500, 500, 200]
    # The two original rows are untouched -- same attempt_no, same response_status as when
    # the chain died.
    assert all_attempts[0].attempt_no == 1
    assert all_attempts[1].attempt_no == 2


async def test_replay_raises_not_found_for_unknown_delivery(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    sessionmaker = _sessionmaker(db_engine)
    service = ReplayService(UnitOfWork(sessionmaker), stream_redis)

    with pytest.raises(NotFoundError):
        await service.replay(uuid.uuid4(), uuid.uuid4())


async def test_replay_raises_not_found_for_cross_tenant_delivery(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    sessionmaker = _sessionmaker(db_engine)
    delivery_id, _owning_tenant_id = await _seed_dead_delivery(sessionmaker)
    service = ReplayService(UnitOfWork(sessionmaker), stream_redis)

    with pytest.raises(NotFoundError):
        await service.replay(delivery_id, uuid.uuid4())


async def test_replay_raises_conflict_for_a_delivery_that_is_not_dead(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    sessionmaker = _sessionmaker(db_engine)
    uow = UnitOfWork(sessionmaker)
    async with uow:
        tenant = await uow.tenants.create(name=f"acme-{uuid.uuid4()}")
        endpoint = await uow.endpoints.create(
            tenant_id=tenant.id,
            url="https://example.com/webhook",
            secret="s3cr3t",
            subscribed_event_types=frozenset({"t"}),
        )
        event = await uow.events.create(
            tenant_id=tenant.id, event_type="t", payload={}, idempotency_key="i"
        )
        delivery = await uow.deliveries.create(event_id=event.id, endpoint_id=endpoint.id)
        await uow.commit()
    service = ReplayService(UnitOfWork(sessionmaker), stream_redis)

    with pytest.raises(DeliveryNotDeadError):
        await service.replay(delivery.id, tenant.id)
