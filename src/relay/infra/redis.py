from collections.abc import AsyncIterator
from functools import lru_cache

from redis.asyncio import Redis

from relay.infra.settings import get_settings


@lru_cache
def get_redis_pool() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


async def get_redis() -> AsyncIterator[Redis]:
    yield get_redis_pool()
