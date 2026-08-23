import json
import uuid
from datetime import datetime
from typing import NamedTuple

from redis.asyncio import Redis

RETRY_ZSET = "relay:deliveries:retry"

# Atomic pop-due: a plain ZRANGEBYSCORE followed by a separate ZREM would let two concurrent
# scheduler instances both read the same due entries before either removes them, firing the
# same retry twice. Wrapping both calls in one script makes the read-then-remove atomic
# against Redis's single-threaded command execution, the same correctness concern SKIP LOCKED
# solves on the Postgres side.
_POP_DUE_SCRIPT = """
local due = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, ARGV[2])
if #due > 0 then
    redis.call('ZREM', KEYS[1], unpack(due))
end
return due
"""


class DueRetry(NamedTuple):
    delivery_id: uuid.UUID
    correlation_id: str | None


async def schedule_retry(
    redis: Redis,
    delivery_id: uuid.UUID,
    next_retry_at: datetime,
    *,
    correlation_id: str | None = None,
    key: str = RETRY_ZSET,
) -> None:
    """The ZSET member is a small JSON envelope, not a bare delivery id, so a retry re-fired
    by the scheduler (relay.workers.scheduler) can carry the same correlation_id its first
    attempt did -- otherwise the id would be lost the moment a delivery backs off, which
    would be most deliveries with more than one attempt.
    """
    member = json.dumps({"delivery_id": str(delivery_id), "correlation_id": correlation_id})
    await redis.zadd(key, {member: next_retry_at.timestamp()})


async def pop_due_retries(
    redis: Redis, now: datetime, *, key: str = RETRY_ZSET, limit: int = 100
) -> list[DueRetry]:
    due = await redis.eval(_POP_DUE_SCRIPT, 1, key, now.timestamp(), limit)
    return [_parse_member(entry) for entry in due]


def _parse_member(member: str) -> DueRetry:
    try:
        payload = json.loads(member)
    except (json.JSONDecodeError, TypeError):
        # A bare UUID string -- a retry scheduled by a pre-Phase-5 process before this
        # JSON envelope existed. Only possible for the short window around a deploy that
        # changes this format; treated as "no correlation id available" rather than an error.
        return DueRetry(delivery_id=uuid.UUID(member), correlation_id=None)
    return DueRetry(
        delivery_id=uuid.UUID(payload["delivery_id"]), correlation_id=payload.get("correlation_id")
    )
