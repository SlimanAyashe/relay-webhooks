import asyncio
import logging
import os
from collections.abc import Callable

from redis.asyncio import Redis

from relay.infra.http_sender import HttpxOutboundSender, OutboundHttpSender
from relay.infra.redis import get_redis_pool
from relay.infra.settings import get_settings
from relay.infra.streams import ack_delivery, claim_stale_deliveries
from relay.repositories.unit_of_work import UnitOfWork, get_unit_of_work
from relay.workers.dispatcher import process_delivery_message
from relay.workers.shutdown import install_sigterm_handler, wait_or_shutdown

logger = logging.getLogger(__name__)

DEFAULT_CLAIM_COUNT = 50


async def run_once(
    redis: Redis,
    http_sender: OutboundHttpSender,
    *,
    consumer_name: str,
    min_idle_ms: int,
    uow_factory: Callable[[], UnitOfWork] = get_unit_of_work,
) -> int:
    """Reclaims stream entries idle longer than `min_idle_ms` -- their original consumer
    died before acking -- under `consumer_name`, reprocesses each through the same delivery
    logic the dispatcher uses, and acks the original message ID once done. This is what
    makes the at-least-once guarantee real: a dispatcher that dies mid-delivery doesn't
    silently drop the message, it just gets reclaimed and retried here.
    """
    claimed = await claim_stale_deliveries(
        redis, consumer_name, min_idle_ms=min_idle_ms, count=DEFAULT_CLAIM_COUNT
    )
    for message_id, delivery_id in claimed:
        try:
            await process_delivery_message(uow_factory, http_sender, redis, delivery_id)
            await ack_delivery(redis, message_id)
        except Exception:
            logger.exception(
                "reaper failed to reprocess message %s (delivery %s); leaving unacked for the "
                "next sweep",
                message_id,
                delivery_id,
            )
    return len(claimed)


async def run_forever(
    *,
    consumer_name: str | None = None,
    tick_interval: float | None = None,
    min_idle_ms: int | None = None,
    http_sender: OutboundHttpSender | None = None,
) -> None:
    settings = get_settings()
    consumer_name = consumer_name or f"reaper-{os.getpid()}"
    tick_interval = (
        tick_interval if tick_interval is not None else settings.reaper_tick_interval_seconds
    )
    min_idle_ms = min_idle_ms if min_idle_ms is not None else settings.reaper_min_idle_ms
    redis = get_redis_pool()
    owned_sender = HttpxOutboundSender() if http_sender is None else None
    sender: OutboundHttpSender = http_sender if http_sender is not None else owned_sender  # type: ignore[assignment]
    shutdown = install_sigterm_handler()

    logger.info(
        "reaper worker %s starting (tick_interval=%ss, min_idle_ms=%s)",
        consumer_name,
        tick_interval,
        min_idle_ms,
    )
    try:
        while not shutdown.is_set():
            reclaimed = await run_once(
                redis, sender, consumer_name=consumer_name, min_idle_ms=min_idle_ms
            )
            if reclaimed:
                logger.warning("reaper reclaimed %s stale delivery message(s)", reclaimed)
            await wait_or_shutdown(shutdown, tick_interval)
    finally:
        if owned_sender is not None:
            await owned_sender.aclose()
    logger.info("reaper worker %s stopped", consumer_name)


if __name__ == "__main__":
    logging.basicConfig(level=get_settings().log_level)
    asyncio.run(run_forever())
