import uuid
from typing import Any, NamedTuple

from redis.asyncio import Redis
from redis.exceptions import ResponseError

DELIVERY_STREAM = "relay:deliveries:stream"
DISPATCH_GROUP = "relay:dispatchers"

_DELIVERY_ID_FIELD = "delivery_id"
_CORRELATION_ID_FIELD = "correlation_id"


class DeliveryMessage(NamedTuple):
    """One delivery-stream entry as read back by the dispatcher or reaper. `correlation_id`
    is the field the message carries it in -- Redis Streams' closest equivalent to a message
    header -- so a worker processing this entry can bind it into its log context and produce
    lines a caller can tie back to the original ingest request. None for a delivery whose
    event predates the correlation_id column, or whose retry-ZSET entry predates this field
    (see relay.infra.retry_schedule's fallback parse).
    """

    message_id: str
    delivery_id: uuid.UUID
    correlation_id: str | None


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
    redis: Redis,
    delivery_id: uuid.UUID,
    *,
    correlation_id: str | None = None,
    stream: str = DELIVERY_STREAM,
) -> str:
    fields = {_DELIVERY_ID_FIELD: str(delivery_id)}
    if correlation_id is not None:
        fields[_CORRELATION_ID_FIELD] = correlation_id
    # redis-py's stub types XADD's fields dict against a broad
    # bytes|bytearray|memoryview|str|int|float union on both key and value; dict is
    # invariant, so a plain dict[str, str] variable doesn't structurally match even though
    # it's a valid subset at runtime -- true of the pre-Phase-5 single-field version of this
    # call too, just masked then by mypy inferring an inline literal contextually.
    message_id = await redis.xadd(stream, fields)  # type: ignore[arg-type]
    return str(message_id)


async def read_deliveries(
    redis: Redis,
    consumer: str,
    *,
    stream: str = DELIVERY_STREAM,
    group: str = DISPATCH_GROUP,
    count: int = 10,
    block_ms: int = 5000,
) -> list[DeliveryMessage]:
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
) -> list[DeliveryMessage]:
    """Reclaims pending entries idle longer than `min_idle_ms` -- the consumer that read them
    died before acking -- under `consumer`'s own name, so this reaper (or dispatcher) can
    reprocess and ack them under the same message IDs. This is what turns the at-least-once
    guarantee from "true unless a worker happens to die" into "true".
    """
    _next_cursor, claimed, _deleted = await redis.xautoclaim(
        stream, group, consumer, min_idle_ms, start_id="0-0", count=count
    )
    return _parse_entries(claimed)


async def stream_depth(redis: Redis, *, stream: str = DELIVERY_STREAM) -> int:
    """Entries on the stream not yet delivered to any consumer group member -- the raw
    backlog size, independent of how many are currently claimed. Sampled by the dispatcher
    into the delivery_queue_depth gauge (relay.infra.metrics)."""
    return int(await redis.xlen(stream))


async def pending_count(
    redis: Redis, *, stream: str = DELIVERY_STREAM, group: str = DISPATCH_GROUP
) -> int:
    """Entries claimed by a consumer but not yet acked -- the delivery_in_flight gauge.
    XPENDING with no range returns a summary dict; `pending` is 0 (not an error) before the
    consumer group has ever read anything.
    """
    summary = await redis.xpending(stream, group)
    return int(summary["pending"]) if summary else 0


def _flatten_stream_response(response: Any) -> list[DeliveryMessage]:
    entries: list[DeliveryMessage] = []
    for _stream_name, messages in response:
        entries.extend(_parse_entries(messages))
    return entries


def _parse_entries(messages: list[tuple[str, dict[str, str]]]) -> list[DeliveryMessage]:
    return [
        DeliveryMessage(
            message_id=message_id,
            delivery_id=uuid.UUID(fields[_DELIVERY_ID_FIELD]),
            correlation_id=fields.get(_CORRELATION_ID_FIELD),
        )
        for message_id, fields in messages
        if fields
    ]
