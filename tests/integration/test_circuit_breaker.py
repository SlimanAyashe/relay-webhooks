import uuid
from datetime import UTC, datetime, timedelta

from freezegun import freeze_time
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from relay.domain.deliveries import DeliveryState
from relay.domain.delivery_attempts import AttemptErrorClass
from relay.domain.endpoints import BreakerState
from relay.infra.http_sender import OutboundHttpResult
from relay.infra.settings import get_settings
from relay.repositories.unit_of_work import UnitOfWork
from relay.workers.dispatcher import process_delivery_message
from tests.fakes import FakeOutboundHttpSender


def _sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _seed_delivery(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID]:
    uow = UnitOfWork(sessionmaker)
    async with uow:
        tenant = await uow.tenants.create(name=f"acme-{uuid.uuid4()}")
        endpoint = await uow.endpoints.create(
            tenant_id=tenant.id,
            url="https://flaky.example.com/webhook",
            secret="s3cr3t",
            subscribed_event_types=frozenset({"order.created"}),
        )
        event = await uow.events.create(
            tenant_id=tenant.id, event_type="order.created", payload={"n": 1}, idempotency_key="i"
        )
        delivery = await uow.deliveries.create(event_id=event.id, endpoint_id=endpoint.id)
        await uow.commit()
    return delivery.id, endpoint.id


async def test_full_breaker_cycle_closed_open_half_open_closed(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    """Testing scenario #10: consecutive failures open the breaker; after the cooldown it
    half-opens for exactly one probe; that probe succeeding closes it again -- driven
    through the real dispatcher entry point (process_delivery_message) against a real
    Postgres-backed endpoint row, with freezegun fast-forwarding past the cooldown.
    """
    settings = get_settings()
    sessionmaker = _sessionmaker(db_engine)
    delivery_id, endpoint_id = await _seed_delivery(sessionmaker)

    failure = OutboundHttpResult(
        latency_ms=5, status_code=500, error_class=AttemptErrorClass.HTTP_ERROR
    )
    success = OutboundHttpResult(latency_ms=5, status_code=200)
    sender = FakeOutboundHttpSender([failure] * settings.breaker_failure_threshold + [success])

    frozen_now = datetime(2026, 1, 1, tzinfo=UTC)
    uow_factory = lambda: UnitOfWork(sessionmaker)  # noqa: E731

    with freeze_time(frozen_now):
        for _ in range(settings.breaker_failure_threshold):
            await process_delivery_message(uow_factory, sender, stream_redis, delivery_id)

        async with UnitOfWork(sessionmaker) as check_uow:
            endpoint = await check_uow.endpoints.get(endpoint_id)
        assert endpoint is not None
        assert endpoint.breaker_state is BreakerState.OPEN
        assert endpoint.consecutive_failures == settings.breaker_failure_threshold
        assert endpoint.opened_at == frozen_now
        assert len(sender.calls) == settings.breaker_failure_threshold

        # Breaker is open and the cooldown hasn't elapsed: this attempt is deferred
        # without ever touching the HTTP sender.
        await process_delivery_message(uow_factory, sender, stream_redis, delivery_id)
        assert len(sender.calls) == settings.breaker_failure_threshold

        async with UnitOfWork(sessionmaker) as check_uow:
            delivery = await check_uow.deliveries.get(delivery_id)
        assert delivery is not None
        assert delivery.state is DeliveryState.RETRYING
        assert delivery.next_retry_at == frozen_now + timedelta(
            seconds=settings.breaker_cooldown_seconds
        )

    # Fast-forward past the cooldown: the next attempt flips OPEN -> HALF_OPEN and allows
    # exactly one probe, which succeeds and closes the breaker.
    resumed_at = frozen_now + timedelta(seconds=settings.breaker_cooldown_seconds)
    with freeze_time(resumed_at):
        await process_delivery_message(uow_factory, sender, stream_redis, delivery_id)

    assert len(sender.calls) == settings.breaker_failure_threshold + 1

    async with UnitOfWork(sessionmaker) as check_uow:
        endpoint = await check_uow.endpoints.get(endpoint_id)
        delivery = await check_uow.deliveries.get(delivery_id)
    assert endpoint is not None
    assert endpoint.breaker_state is BreakerState.CLOSED
    assert endpoint.consecutive_failures == 0
    assert endpoint.opened_at is None
    assert delivery is not None
    assert delivery.state is DeliveryState.DELIVERED
