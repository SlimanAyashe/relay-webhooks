import asyncio
import logging
import os
import uuid
from collections.abc import Callable

from redis.asyncio import Redis

from relay.domain.deliveries import DeliveryState
from relay.infra.http_sender import HttpxOutboundSender, OutboundHttpSender
from relay.infra.redis import get_redis_pool
from relay.infra.retry_schedule import schedule_retry
from relay.infra.settings import get_settings
from relay.infra.streams import ack_delivery, ensure_consumer_group, read_deliveries
from relay.repositories.unit_of_work import UnitOfWork, get_unit_of_work
from relay.services.deliveries.service import DeliveryAttemptService
from relay.workers.shutdown import install_sigterm_handler

logger = logging.getLogger(__name__)


async def process_delivery_message(
    uow_factory: Callable[[], UnitOfWork],
    http_sender: OutboundHttpSender,
    redis: Redis,
    delivery_id: uuid.UUID,
) -> None:
    """Runs one delivery attempt and, if it needs a retry, schedules it in the Redis ZSET.
    Shared by the dispatcher's normal consumer loop and the reaper's reclaimed-message path
    -- both are ultimately just "process this delivery_id" once they have one in hand.
    """
    service = DeliveryAttemptService(uow_factory(), http_sender)
    delivery = await service.attempt(delivery_id)
    if delivery.state is DeliveryState.RETRYING and delivery.next_retry_at is not None:
        await schedule_retry(redis, delivery.id, delivery.next_retry_at)


async def run_forever(
    *,
    consumer_name: str | None = None,
    concurrency: int | None = None,
    http_sender: OutboundHttpSender | None = None,
    uow_factory: Callable[[], UnitOfWork] = get_unit_of_work,
) -> None:
    settings = get_settings()
    consumer_name = consumer_name or f"dispatcher-{os.getpid()}"
    concurrency = concurrency or settings.dispatcher_concurrency
    redis = get_redis_pool()
    owned_sender = HttpxOutboundSender() if http_sender is None else None
    sender: OutboundHttpSender = http_sender if http_sender is not None else owned_sender  # type: ignore[assignment]
    shutdown = install_sigterm_handler()
    await ensure_consumer_group(redis)

    semaphore = asyncio.Semaphore(concurrency)
    in_flight: set[asyncio.Task[None]] = set()

    logger.info("dispatcher worker %s starting (concurrency=%s)", consumer_name, concurrency)
    try:
        while not shutdown.is_set():
            messages = await read_deliveries(redis, consumer_name, count=concurrency, block_ms=1000)
            for message_id, delivery_id in messages:
                await semaphore.acquire()
                task = asyncio.create_task(
                    _handle_and_ack(redis, sender, message_id, delivery_id, semaphore, uow_factory)
                )
                in_flight.add(task)
                task.add_done_callback(in_flight.discard)
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)
    finally:
        if owned_sender is not None:
            await owned_sender.aclose()
    logger.info("dispatcher worker %s stopped", consumer_name)


async def _handle_and_ack(
    redis: Redis,
    http_sender: OutboundHttpSender,
    message_id: str,
    delivery_id: uuid.UUID,
    semaphore: asyncio.Semaphore,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    try:
        await process_delivery_message(uow_factory, http_sender, redis, delivery_id)
        await ack_delivery(redis, message_id)
    except Exception:
        logger.exception(
            "delivery attempt failed for message %s (delivery %s)", message_id, delivery_id
        )
        # Deliberately not acked -- the reaper's XAUTOCLAIM sweep reclaims and retries this
        # message once it's been idle past REAPER_MIN_IDLE_MS.
    finally:
        semaphore.release()


if __name__ == "__main__":
    logging.basicConfig(level=get_settings().log_level)
    asyncio.run(run_forever())
