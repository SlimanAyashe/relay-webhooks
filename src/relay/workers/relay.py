import asyncio
import logging

from redis.asyncio import Redis

from relay.domain.outbox import OutboxEntry
from relay.infra.logging import configure_logging
from relay.infra.redis import get_redis_pool
from relay.infra.settings import get_settings
from relay.infra.streams import enqueue_delivery
from relay.repositories.unit_of_work import UnitOfWork, get_unit_of_work
from relay.workers.shutdown import install_sigterm_handler, wait_or_shutdown

logger = logging.getLogger(__name__)


async def run_once(uow: UnitOfWork, redis: Redis, *, batch_size: int) -> int:
    """Claims due outbox rows, fans out one Delivery row + one stream message per endpoint
    subscribed to that event's type, then marks each outbox row processed -- all inside one
    unit of work, so a crash anywhere in the middle rolls the whole batch back to `pending`
    rather than leaving a partial fan-out committed. Returns the number of outbox rows
    claimed (0 means nothing was due).
    """
    async with uow:
        entries = await uow.outbox.claim_due(limit=batch_size)
        for entry in entries:
            await _process_entry(uow, redis, entry)
        await uow.commit()
    return len(entries)


async def _process_entry(uow: UnitOfWork, redis: Redis, entry: OutboxEntry) -> None:
    event = await uow.events.get(entry.event_id)
    if event is None:
        # Unreachable in normal operation -- the outbox row and its event are written in
        # the same transaction by EventIngestService -- but a missing event can't be
        # retried into existence, so mark it processed rather than looping on it forever.
        logger.error("outbox entry %s references missing event %s", entry.id, entry.event_id)
        await uow.outbox.mark_processed(entry.id)
        return

    endpoints = await uow.endpoints.list_active_subscribed(event.tenant_id, event.type)
    for endpoint in endpoints:
        delivery = await uow.deliveries.create(event_id=event.id, endpoint_id=endpoint.id)
        await enqueue_delivery(redis, delivery.id, correlation_id=event.correlation_id)
    await uow.outbox.mark_processed(entry.id)


async def run_forever(*, batch_size: int | None = None, poll_interval: float | None = None) -> None:
    settings = get_settings()
    batch_size = batch_size or settings.outbox_claim_batch_size
    poll_interval = (
        poll_interval if poll_interval is not None else settings.outbox_poll_interval_seconds
    )
    redis = get_redis_pool()
    shutdown = install_sigterm_handler()

    logger.info(
        "relay worker starting (batch_size=%s, poll_interval=%ss)", batch_size, poll_interval
    )
    while not shutdown.is_set():
        processed = await run_once(get_unit_of_work(), redis, batch_size=batch_size)
        if processed == 0:
            await wait_or_shutdown(shutdown, poll_interval)
    logger.info("relay worker stopped")


if __name__ == "__main__":
    configure_logging(get_settings().log_level)
    asyncio.run(run_forever())
