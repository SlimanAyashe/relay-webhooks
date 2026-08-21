import asyncio
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis

from relay.infra.attempt_events import AttemptEvent, publish_attempt_event, subscribe_attempt_events


def _event(delivery_id: uuid.UUID) -> AttemptEvent:
    return AttemptEvent(
        delivery_id=delivery_id,
        endpoint_id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        attempt_no=1,
        delivery_state="delivered",
        latency_ms=42,
        response_status=200,
        error_class=None,
        next_retry_at=None,
        breaker_state="closed",
        created_at=datetime.now(UTC),
    )


async def test_subscriber_receives_only_events_published_for_its_own_tenant(
    stream_redis: Redis,
) -> None:
    """Testing scenario (Phase 4, backlog p4-27): concurrent sandboxes publishing on the
    same Redis instance never cross-deliver -- isolation is structural (one channel per
    tenant id), not a filter that could be forgotten.
    """
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    delivery_a = uuid.uuid4()
    delivery_b = uuid.uuid4()

    received_a: list[str] = []

    async def _collect_one() -> None:
        async for payload in subscribe_attempt_events(stream_redis, tenant_a):
            received_a.append(payload)
            return

    task = asyncio.create_task(_collect_one())
    await asyncio.sleep(0.2)  # let the subscription establish before anything is published

    await publish_attempt_event(stream_redis, tenant_b, _event(delivery_b))
    await publish_attempt_event(stream_redis, tenant_a, _event(delivery_a))

    await asyncio.wait_for(task, timeout=5)

    assert len(received_a) == 1
    assert str(delivery_a) in received_a[0]
    assert str(delivery_b) not in received_a[0]


async def test_subscriber_receives_multiple_events_in_publish_order(stream_redis: Redis) -> None:
    tenant_id = uuid.uuid4()
    deliveries = [uuid.uuid4() for _ in range(3)]
    received: list[str] = []

    async def _collect_three() -> None:
        async for payload in subscribe_attempt_events(stream_redis, tenant_id):
            received.append(payload)
            if len(received) == 3:
                return

    task = asyncio.create_task(_collect_three())
    await asyncio.sleep(0.2)

    for delivery_id in deliveries:
        await publish_attempt_event(stream_redis, tenant_id, _event(delivery_id))

    await asyncio.wait_for(task, timeout=5)

    assert [str(d) in payload for d, payload in zip(deliveries, received, strict=True)] == [
        True,
        True,
        True,
    ]
