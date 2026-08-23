"""Backlog item 13 (Phase 5): a single correlation_id generated at event ingest must appear
on the ingest log line and on every worker log line produced while delivering that event --
proving relay.api.middleware.TraceIdMiddleware's id actually spans the process boundary via
relay.domain.events.Event.correlation_id -> the Redis stream message fields
(relay.infra.streams) -> relay.workers.dispatcher's structlog contextvars binding, not just
living inside one process.

Uses structlog.testing.capture_logs() rather than capturing real stdout: it intercepts every
structlog-native call process-wide (including ones made from the TestClient's own portal
thread) by swapping in a capturing processor, which is simpler and more robust here than
racing capsys against threads and event loops.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from relay.infra.http_sender import OutboundHttpResult
from relay.infra.streams import read_deliveries
from relay.repositories.unit_of_work import UnitOfWork
from relay.workers.dispatcher import _handle_and_ack
from relay.workers.relay import run_once
from tests.fakes import FakeOutboundHttpSender

AuthHeaders = Callable[[frozenset[str]], Awaitable[tuple[uuid.UUID, dict[str, str]]]]


async def test_correlation_id_spans_ingest_log_line_and_worker_log_lines(
    wired_client: TestClient,
    auth_headers: AuthHeaders,
    postgres_url: str,
    stream_redis: Redis,
) -> None:
    _tenant_id, headers = await auth_headers(frozenset({"*"}))
    wired_client.post(
        "/v1/endpoints",
        json={"url": "https://example.com/hook", "subscribed_event_types": ["order.created"]},
        headers=headers,
    )
    trace_id = f"corr-{uuid.uuid4()}"

    with structlog.testing.capture_logs(
        processors=[structlog.contextvars.merge_contextvars]
    ) as ingest_logs:
        ingest_response = wired_client.post(
            "/v1/events",
            json={"type": "order.created", "payload": {"order_id": "1"}},
            headers={**headers, "Idempotency-Key": "idem-corr-1", "X-Trace-Id": trace_id},
        )
    assert ingest_response.status_code == 202
    assert ingest_response.headers["X-Trace-Id"] == trace_id
    assert any(entry.get("correlation_id") == trace_id for entry in ingest_logs), ingest_logs

    # Fan the event out to a delivery + Redis stream message the way relay.workers.relay
    # would, using its own engine (never the FastAPI-injected one -- see wired_client's own
    # docstring on why: asyncpg connections can't cross event loops).
    engine = create_async_engine(postgres_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        processed = await run_once(UnitOfWork(sessionmaker), stream_redis, batch_size=10)
        assert processed == 1

        messages = await read_deliveries(stream_redis, "corr-test-consumer", count=10, block_ms=100)
        assert len(messages) == 1
        assert messages[0].correlation_id == trace_id

        sender = FakeOutboundHttpSender([OutboundHttpResult(latency_ms=5, status_code=200)])

        with structlog.testing.capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as worker_logs:
            await _handle_and_ack(
                stream_redis,
                sender,
                messages[0],
                asyncio.Semaphore(1),
                lambda: UnitOfWork(sessionmaker),
            )
    finally:
        await engine.dispose()

    assert worker_logs, "expected at least one structured log line from the worker path"
    assert all(entry.get("correlation_id") == trace_id for entry in worker_logs), worker_logs
