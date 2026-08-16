"""Redis-backed per-tenant token-bucket rate limiter. Keyed by tenant id so it stays
correct across multiple API replicas sharing one Redis -- the same reason the retry
schedule's pop-due (relay.infra.retry_schedule) and the outbox's SKIP LOCKED claim are
each a single atomic operation rather than a separate read then write.
"""

import time
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis

RATE_LIMIT_KEY_PREFIX = "relay:ratelimit:tenant:"

# A read-refill-spend-write cycle done as separate Redis calls would let two concurrent API
# replicas both read the same bucket before either writes it back, each granting a token
# that should only exist once. Wrapping the whole cycle in one script makes it atomic
# against Redis's single-threaded command execution.
_TOKEN_BUCKET_SCRIPT = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local bucket = redis.call('HMGET', key, 'tokens', 'updated_at')
local tokens = tonumber(bucket[1])
local updated_at = tonumber(bucket[2])
if tokens == nil then
    tokens = burst
    updated_at = now
end

local elapsed = now - updated_at
if elapsed < 0 then
    elapsed = 0
end
tokens = math.min(burst, tokens + elapsed * rate)

local allowed = 0
local retry_after = 0
if tokens >= cost then
    tokens = tokens - cost
    allowed = 1
else
    retry_after = (cost - tokens) / rate
end

redis.call('HMSET', key, 'tokens', tokens, 'updated_at', now)
local ttl = math.ceil(burst / rate) + 1
redis.call('EXPIRE', key, ttl)

return {allowed, tostring(retry_after), tostring(tokens)}
"""


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: float
    remaining_tokens: float


async def allow(
    redis: Redis,
    tenant_id: uuid.UUID,
    *,
    rate_per_second: float,
    burst: int,
    cost: float = 1.0,
    now: float | None = None,
) -> RateLimitDecision:
    """Spends `cost` tokens (default 1, one request) from `tenant_id`'s bucket, refilling
    it for elapsed time since it was last touched, capped at `burst`. Returns whether the
    request is allowed and, if not, how long until the bucket has enough tokens again.
    """
    key = f"{RATE_LIMIT_KEY_PREFIX}{tenant_id}"
    current = now if now is not None else time.time()
    allowed_flag, retry_after_raw, remaining_raw = await redis.eval(
        _TOKEN_BUCKET_SCRIPT, 1, key, rate_per_second, burst, current, cost
    )
    return RateLimitDecision(
        allowed=bool(int(allowed_flag)),
        retry_after_seconds=max(0.0, float(retry_after_raw)),
        remaining_tokens=float(remaining_raw),
    )
