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
    per_endpoint_concurrency: int | None = None,
    http_sender: OutboundHttpSender | None = None,
    uow_factory: Callable[[], UnitOfWork] = get_unit_of_work,
) -> None:
    settings = get_settings()
    consumer_name = consumer_name or f"dispatcher-{os.getpid()}"
    concurrency = concurrency or settings.dispatcher_concurrency
    per_endpoint_concurrency = (
        per_endpoint_concurrency or settings.dispatcher_per_endpoint_concurrency
    )
    redis = get_redis_pool()
    owned_sender = HttpxOutboundSender() if http_sender is None else None
    sender: OutboundHttpSender = http_sender if http_sender is not None else owned_sender  # type: ignore[assignment]
    shutdown = install_sigterm_handler()
    await ensure_consumer_group(redis)

    semaphore = asyncio.Semaphore(concurrency)
    # Created lazily, one per endpoint id seen so far, held for the duration of one
    # delivery attempt against that endpoint -- so a single dead/slow destination can't
    # consume the whole pool's concurrency budget even when the pool has room. Roughly 80%
    # of the benefit of full per-tenant fair scheduling for a fraction of the mechanism;
    # see docs/adr/0005-phase-3-security-resilience.md for the tradeoff.
    endpoint_semaphores: dict[uuid.UUID, asyncio.Semaphore] = {}
    in_flight: set[asyncio.Task[None]] = set()

    def endpoint_semaphore(endpoint_id: uuid.UUID) -> asyncio.Semaphore:
        sem = endpoint_semaphores.get(endpoint_id)
        if sem is None:
            sem = asyncio.Semaphore(per_endpoint_concurrency)
            endpoint_semaphores[endpoint_id] = sem
        return sem

    logger.info(
        "dispatcher worker %s starting (concurrency=%s, per_endpoint_concurrency=%s)",
        consumer_name,
        concurrency,
        per_endpoint_concurrency,
    )
    try:
        while not shutdown.is_set():
            messages = await read_deliveries(redis, consumer_name, count=concurrency, block_ms=1000)
            for message_id, delivery_id in messages:
                await semaphore.acquire()
                task = asyncio.create_task(
                    _handle_and_ack(
                        redis,
                        sender,
                        message_id,
                        delivery_id,
                        semaphore,
                        uow_factory,
                        endpoint_semaphore,
                    )
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
    endpoint_semaphore: Callable[[uuid.UUID], asyncio.Semaphore] | None = None,
) -> None:
    acquired_endpoint_sem: asyncio.Semaphore | None = None
    try:
        if endpoint_semaphore is not None:
            endpoint_id = await _lookup_endpoint_id(uow_factory, delivery_id)
            if endpoint_id is not None:
                acquired_endpoint_sem = endpoint_semaphore(endpoint_id)
                await acquired_endpoint_sem.acquire()

        await process_delivery_message(uow_factory, http_sender, redis, delivery_id)
        await ack_delivery(redis, message_id)
    except Exception:
        logger.exception(
            "delivery attempt failed for message %s (delivery %s)", message_id, delivery_id
        )
        # Deliberately not acked -- the reaper's XAUTOCLAIM sweep reclaims and retries this
        # message once it's been idle past REAPER_MIN_IDLE_MS.
    finally:
        if acquired_endpoint_sem is not None:
            acquired_endpoint_sem.release()
        semaphore.release()


async def _lookup_endpoint_id(
    uow_factory: Callable[[], UnitOfWork], delivery_id: uuid.UUID
) -> uuid.UUID | None:
    """A cheap lookup so the per-endpoint semaphore can be acquired before the real
    attempt logic runs. If the delivery doesn't exist (or the lookup itself fails), fall
    through with no per-endpoint gating -- process_delivery_message's own NotFoundError
    handling (surfaced via the except Exception above) is still the source of truth.
    """
    uow = uow_factory()
    async with uow:
        delivery = await uow.deliveries.get(delivery_id)
    return delivery.endpoint_id if delivery is not None else None


if __name__ == "__main__":
    logging.basicConfig(level=get_settings().log_level)
    asyncio.run(run_forever())
