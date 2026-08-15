import uuid
from datetime import datetime

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


async def schedule_retry(
    redis: Redis, delivery_id: uuid.UUID, next_retry_at: datetime, *, key: str = RETRY_ZSET
) -> None:
    await redis.zadd(key, {str(delivery_id): next_retry_at.timestamp()})


async def pop_due_retries(
    redis: Redis, now: datetime, *, key: str = RETRY_ZSET, limit: int = 100
) -> list[uuid.UUID]:
    due = await redis.eval(_POP_DUE_SCRIPT, 1, key, now.timestamp(), limit)
    return [uuid.UUID(entry) for entry in due]
