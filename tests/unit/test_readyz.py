from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay.infra.db import get_session
from relay.infra.redis import get_redis


class _FailingSession:
    async def execute(self, *args: object, **kwargs: object) -> None:
        raise ConnectionError("postgres unreachable")


class _FailingRedis:
    async def ping(self) -> None:
        raise ConnectionError("redis unreachable")


async def _failing_session() -> AsyncIterator[_FailingSession]:
    yield _FailingSession()


async def _failing_redis() -> AsyncIterator[_FailingRedis]:
    yield _FailingRedis()


def test_readyz_returns_503_when_dependencies_are_down(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[get_session] = _failing_session
    app.dependency_overrides[get_redis] = _failing_redis

    response = client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert "postgres unreachable" in body["checks"]["postgres"]
    assert "redis unreachable" in body["checks"]["redis"]

    app.dependency_overrides.clear()
