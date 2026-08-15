import uuid
from datetime import UTC, datetime

import httpx
import respx
from freezegun import freeze_time
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from relay.domain.deliveries import DeliveryState
from relay.domain.delivery_attempts import AttemptErrorClass
from relay.infra.http_sender import HttpxOutboundSender, OutboundHttpResult
from relay.infra.retry_schedule import RETRY_ZSET
from relay.repositories.delivery_attempts.models import DeliveryAttemptModel
from relay.repositories.unit_of_work import UnitOfWork
from relay.workers.dispatcher import process_delivery_message
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
            tenant_id=tenant.id, event_type="order.created", payload={"n": 1}, idempotency_key="i"
        )
        delivery = await uow.deliveries.create(event_id=event.id, endpoint_id=endpoint.id)
        await uow.commit()
    return delivery.id


async def test_process_delivery_message_marks_delivered_on_2xx(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    sessionmaker = _sessionmaker(db_engine)
    delivery_id = await _seed_delivery(sessionmaker)
    sender = FakeOutboundHttpSender([OutboundHttpResult(latency_ms=5, status_code=200)])

    await process_delivery_message(
        lambda: UnitOfWork(sessionmaker), sender, stream_redis, delivery_id
    )

    async with UnitOfWork(sessionmaker) as check_uow:
        delivery = await check_uow.deliveries.get(delivery_id)
        assert delivery is not None
        assert delivery.state is DeliveryState.DELIVERED
    assert await stream_redis.zcard(RETRY_ZSET) == 0


async def test_process_delivery_message_on_500_schedules_jittered_backoff(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    """Testing scenario #7: a downstream 500 schedules a retry whose next_retry_at falls
    within the full-jitter backoff bound for the first retry (base_seconds=1.0), not at some
    arbitrary or fixed delay.
    """
    sessionmaker = _sessionmaker(db_engine)
    delivery_id = await _seed_delivery(sessionmaker)
    sender = FakeOutboundHttpSender(
        [
            OutboundHttpResult(
                latency_ms=5, status_code=500, error_class=AttemptErrorClass.HTTP_ERROR
            )
        ]
    )
    frozen_now = datetime(2026, 1, 1, tzinfo=UTC)

    with freeze_time(frozen_now):
        await process_delivery_message(
            lambda: UnitOfWork(sessionmaker), sender, stream_redis, delivery_id
        )

    async with UnitOfWork(sessionmaker) as check_uow:
        delivery = await check_uow.deliveries.get(delivery_id)
        assert delivery is not None
        assert delivery.state is DeliveryState.RETRYING
        assert delivery.next_retry_at is not None
        delay = (delivery.next_retry_at - frozen_now).total_seconds()
        assert 0 <= delay < 1.0  # attempt 0 -> bound = base_seconds (1.0)

    due_ids = [
        entry
        for entry in await stream_redis.zrangebyscore(RETRY_ZSET, "-inf", "+inf")
        if uuid.UUID(entry) == delivery_id
    ]
    assert due_ids == [str(delivery_id)]


async def test_process_delivery_message_records_timeout_not_success(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    """Testing scenario #8: a downstream that times out mid-response is recorded with
    error_class=timeout and the delivery moves to retrying, never delivered.
    """
    sessionmaker = _sessionmaker(db_engine)
    delivery_id = await _seed_delivery(sessionmaker)
    sender = FakeOutboundHttpSender(
        [OutboundHttpResult(latency_ms=10_000, error_class=AttemptErrorClass.TIMEOUT)]
    )

    await process_delivery_message(
        lambda: UnitOfWork(sessionmaker), sender, stream_redis, delivery_id
    )

    async with UnitOfWork(sessionmaker) as check_uow:
        delivery = await check_uow.deliveries.get(delivery_id)
        assert delivery is not None
        assert delivery.state is DeliveryState.RETRYING
        # A raw query independent of DeliveryAttemptRepository, so a bug in the repository
        # can't also hide its own evidence.
        attempts = await check_uow._session.execute(
            select(DeliveryAttemptModel).where(DeliveryAttemptModel.delivery_id == delivery_id)
        )
        rows = list(attempts.scalars())
        assert len(rows) == 1
        assert rows[0].error_class == "timeout"
        assert rows[0].response_status is None


@respx.mock
async def test_httpx_sender_classifies_a_real_timeout() -> None:
    """Exercises the real HttpxOutboundSender adapter (not the fake) against a mocked
    transport, per the plan's testing mechanics: respx for outbound HTTP.
    """
    respx.post("https://example.com/webhook").mock(side_effect=httpx.ReadTimeout("timed out"))
    sender = HttpxOutboundSender()

    result = await sender.send(url="https://example.com/webhook", payload=b"{}", headers={})
    await sender.aclose()

    assert result.error_class is AttemptErrorClass.TIMEOUT
    assert result.status_code is None


@respx.mock
async def test_httpx_sender_classifies_a_connection_error() -> None:
    respx.post("https://example.com/webhook").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    sender = HttpxOutboundSender()

    result = await sender.send(url="https://example.com/webhook", payload=b"{}", headers={})
    await sender.aclose()

    assert result.error_class is AttemptErrorClass.CONNECTION_ERROR


@respx.mock
async def test_httpx_sender_classifies_a_non_2xx_as_http_error() -> None:
    respx.post("https://example.com/webhook").mock(return_value=httpx.Response(500, text="boom"))
    sender = HttpxOutboundSender()

    result = await sender.send(url="https://example.com/webhook", payload=b"{}", headers={})
    await sender.aclose()

    assert result.error_class is AttemptErrorClass.HTTP_ERROR
    assert result.status_code == 500
    assert result.response_snippet == "boom"


async def test_redelivery_after_a_post_send_pre_ack_crash_produces_an_observable_duplicate(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    """Testing scenario #4: a worker sends the HTTP request and it succeeds, but the
    worker dies before acking the stream message. The message stays in the PEL and gets
    reclaimed (by the reaper, or here, directly re-run) -- and because the delivery was
    never acked, it gets attempted again, so the receiver observes a genuine duplicate.
    This is precisely why receivers must be idempotent: at-least-once delivery makes this
    outcome expected, not a bug.
    """
    sessionmaker = _sessionmaker(db_engine)
    delivery_id = await _seed_delivery(sessionmaker)
    sender = FakeOutboundHttpSender(
        [
            OutboundHttpResult(latency_ms=5, status_code=200),
            OutboundHttpResult(latency_ms=5, status_code=200),
        ]
    )

    # First attempt: the HTTP call "lands" (the receiver got it) but we deliberately don't
    # ack -- standing in for the worker process dying right after the response came back.
    await process_delivery_message(
        lambda: UnitOfWork(sessionmaker), sender, stream_redis, delivery_id
    )

    # Recovery: the message was never acked, so reprocessing it (what the reaper's
    # XAUTOCLAIM reclaim ultimately triggers) attempts delivery again.
    await process_delivery_message(
        lambda: UnitOfWork(sessionmaker), sender, stream_redis, delivery_id
    )

    assert len(sender.calls) == 2  # the receiver saw the event twice
    async with UnitOfWork(sessionmaker) as check_uow:
        delivery = await check_uow.deliveries.get(delivery_id)
        assert delivery is not None
        assert delivery.attempt_count == 2  # both attempts are recorded, not deduplicated
