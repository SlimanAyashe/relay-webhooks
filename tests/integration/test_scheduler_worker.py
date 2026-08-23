import uuid
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from relay.infra.retry_schedule import schedule_retry
from relay.infra.streams import read_deliveries
from relay.workers.scheduler import run_once


async def test_run_once_fires_only_due_retries(stream_redis: Redis) -> None:
    """Testing scenario #9: once a retry-ZSET entry's next_retry_at is due, the scheduler
    pops it and re-XADDs it onto the delivery stream exactly once -- and leaves entries that
    aren't due yet untouched.
    """
    due_id = uuid.uuid4()
    not_due_id = uuid.uuid4()
    now = datetime.now(UTC)
    await schedule_retry(
        stream_redis, due_id, now - timedelta(seconds=5), correlation_id="corr-abc"
    )
    await schedule_retry(stream_redis, not_due_id, now + timedelta(minutes=5))

    fired = await run_once(stream_redis, limit=100)

    assert fired == 1
    messages = await read_deliveries(stream_redis, "test-consumer", count=10, block_ms=100)
    delivered_ids = {message.delivery_id for message in messages}
    assert delivered_ids == {due_id}
    # The retry-ZSET hop must not drop the correlation id -- otherwise a delivery's second
    # attempt onward would silently lose its trace, undermining the whole point of Phase 5's
    # correlation-id propagation for any event that ever needs more than one attempt.
    assert next(m.correlation_id for m in messages if m.delivery_id == due_id) == "corr-abc"


async def test_run_once_returns_zero_when_nothing_is_due(stream_redis: Redis) -> None:
    not_due_id = uuid.uuid4()
    await schedule_retry(stream_redis, not_due_id, datetime.now(UTC) + timedelta(minutes=5))

    fired = await run_once(stream_redis, limit=100)

    assert fired == 0
