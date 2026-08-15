import asyncio
import logging
from datetime import UTC, datetime

from redis.asyncio import Redis

from relay.infra.redis import get_redis_pool
from relay.infra.retry_schedule import pop_due_retries
from relay.infra.settings import get_settings
from relay.infra.streams import enqueue_delivery
from relay.workers.shutdown import install_sigterm_handler, wait_or_shutdown

logger = logging.getLogger(__name__)

DEFAULT_POP_LIMIT = 100


async def run_once(redis: Redis, *, limit: int = DEFAULT_POP_LIMIT) -> int:
    """Pops every retry-ZSET entry whose score (`next_retry_at`) is now due and re-XADDs
    each back onto the delivery stream, so a retry flows through the exact same
    consumer-group path -- and the exact same XAUTOCLAIM crash-recovery guarantee -- as a
    fresh delivery. Returns the number of retries fired.
    """
    due = await pop_due_retries(redis, datetime.now(UTC), limit=limit)
    for delivery_id in due:
        await enqueue_delivery(redis, delivery_id)
    return len(due)


async def run_forever(
    *, tick_interval: float | None = None, limit: int = DEFAULT_POP_LIMIT
) -> None:
    settings = get_settings()
    tick_interval = (
        tick_interval if tick_interval is not None else settings.scheduler_tick_interval_seconds
    )
    redis = get_redis_pool()
    shutdown = install_sigterm_handler()

    logger.info("scheduler worker starting (tick_interval=%ss)", tick_interval)
    while not shutdown.is_set():
        fired = await run_once(redis, limit=limit)
        if fired:
            logger.info("scheduler fired %s due retries", fired)
        await wait_or_shutdown(shutdown, tick_interval)
    logger.info("scheduler worker stopped")


if __name__ == "__main__":
    logging.basicConfig(level=get_settings().log_level)
    asyncio.run(run_forever())
