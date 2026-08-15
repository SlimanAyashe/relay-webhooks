import uuid
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError

DELIVERY_STREAM = "relay:deliveries:stream"
DISPATCH_GROUP = "relay:dispatchers"

_DELIVERY_ID_FIELD = "delivery_id"


async def ensure_consumer_group(
    redis: Redis, *, stream: str = DELIVERY_STREAM, group: str = DISPATCH_GROUP
) -> None:
    """Idempotent: `XGROUP CREATE ... MKSTREAM` also creates the stream if it doesn't exist
    yet, and a `BUSYGROUP` reply (group already exists) is the expected steady-state case on
    every worker restart, not an error.
    """
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def enqueue_delivery(
    redis: Redis, delivery_id: uuid.UUID, *, stream: str = DELIVERY_STREAM
) -> str:
    message_id = await redis.xadd(stream, {_DELIVERY_ID_FIELD: str(delivery_id)})
    return str(message_id)


async def read_deliveries(
    redis: Redis,
    consumer: str,
    *,
    stream: str = DELIVERY_STREAM,
    group: str = DISPATCH_GROUP,
    count: int = 10,
    block_ms: int = 5000,
) -> list[tuple[str, uuid.UUID]]:
    """Reads up to `count` never-before-delivered messages for this consumer group, blocking
    up to `block_ms` if none are available yet.
    """
    response = await redis.xreadgroup(
        group, consumer, streams={stream: ">"}, count=count, block=block_ms
    )
    return _flatten_stream_response(response)


async def ack_delivery(
    redis: Redis, message_id: str, *, stream: str = DELIVERY_STREAM, group: str = DISPATCH_GROUP
) -> None:
    await redis.xack(stream, group, message_id)


async def claim_stale_deliveries(
    redis: Redis,
    consumer: str,
    *,
    stream: str = DELIVERY_STREAM,
    group: str = DISPATCH_GROUP,
    min_idle_ms: int,
    count: int = 50,
) -> list[tuple[str, uuid.UUID]]:
    """Reclaims pending entries idle longer than `min_idle_ms` -- the consumer that read them
    died before acking -- under `consumer`'s own name, so this reaper (or dispatcher) can
    reprocess and ack them under the same message IDs. This is what turns the at-least-once
    guarantee from "true unless a worker happens to die" into "true".
    """
    _next_cursor, claimed, _deleted = await redis.xautoclaim(
        stream, group, consumer, min_idle_ms, start_id="0-0", count=count
    )
    return _parse_entries(claimed)


def _flatten_stream_response(response: Any) -> list[tuple[str, uuid.UUID]]:
    entries: list[tuple[str, uuid.UUID]] = []
    for _stream_name, messages in response:
        entries.extend(_parse_entries(messages))
    return entries


def _parse_entries(messages: list[tuple[str, dict[str, str]]]) -> list[tuple[str, uuid.UUID]]:
    return [
        (message_id, uuid.UUID(fields[_DELIVERY_ID_FIELD]))
        for message_id, fields in messages
        if fields
    ]
