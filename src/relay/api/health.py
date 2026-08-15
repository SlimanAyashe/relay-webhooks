import asyncio

from fastapi import APIRouter, Depends, Response
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from relay.infra.db import get_session
from relay.infra.redis import get_redis

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _check_postgres(session: AsyncSession) -> str:
    await session.execute(text("SELECT 1"))
    return "ok"


async def _check_redis(redis: Redis) -> str:
    await redis.ping()
    return "ok"


@router.get("/readyz")
async def readyz(
    response: Response,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, object]:
    postgres_result, redis_result = await asyncio.gather(
        _check_postgres(session), _check_redis(redis), return_exceptions=True
    )

    checks = {
        "postgres": postgres_result if isinstance(postgres_result, str) else str(postgres_result),
        "redis": redis_result if isinstance(redis_result, str) else str(redis_result),
    }
    healthy = isinstance(postgres_result, str) and isinstance(redis_result, str)

    response.status_code = 200 if healthy else 503
    return {"status": "ok" if healthy else "error", "checks": checks}
