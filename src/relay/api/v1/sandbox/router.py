import asyncio
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

from relay.api.auth import AuthContext, require_scope, require_scope_allow_query_key
from relay.api.openapi import problem_responses
from relay.api.v1.sandbox.schemas import (
    MetricsRead,
    SandboxCreated,
    SignatureVerifyRequest,
    SignatureVerifyResponse,
)
from relay.domain.errors import RateLimitExceeded
from relay.infra.attempt_events import subscribe_attempt_events
from relay.infra.rate_limit import allow as rate_limit_allow
from relay.infra.redis import get_redis
from relay.infra.settings import Settings, get_settings
from relay.infra.signing import verify
from relay.repositories.unit_of_work import UnitOfWork, get_unit_of_work
from relay.services.deliveries.metrics_service import MetricsService
from relay.services.sandbox.service import SandboxService

router = APIRouter(prefix="/v1/sandbox", tags=["sandbox"])

_require_read = require_scope("endpoints:read")
_require_read_stream = require_scope_allow_query_key("endpoints:read")

# POST /v1/sandbox creates an identity, so it can't itself require an API key -- rate
# limiting is by client IP instead. relay.infra.rate_limit.allow() is keyed on a UUID
# (ordinarily a tenant id) purely to build a Redis key string; uuid5 over the IP address
# gives a stable per-IP key without needing a second limiter implementation.
_SANDBOX_IP_NAMESPACE = uuid.UUID("6f6b0f0a-3b8e-4b7a-9c1a-2e6f9c9d2b7a")

# SSE keep-alive: a comment line (per the SSE spec, ignored by EventSource) sent on this
# interval so a connection idle between attempts doesn't look dead to an intermediary
# proxy/load balancer, and so a disconnected client is detected promptly.
_SSE_HEARTBEAT_SECONDS = 15.0


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


@router.post(
    "",
    status_code=201,
    response_model=SandboxCreated,
    responses=problem_responses(429),
    summary="Provision a sandbox tenant and API key",
    description=(
        "No account needed: creates a scoped, TTL-limited (60 min) tenant and API key "
        "with hard-capped quotas (max endpoints, max events, a tight per-second rate "
        "limit), for driving the public demo console. Rate-limited per client IP."
    ),
)
async def create_sandbox(
    request: Request,
    uow: UnitOfWork = Depends(get_unit_of_work),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
) -> SandboxCreated:
    ip_key = uuid.uuid5(_SANDBOX_IP_NAMESPACE, _client_ip(request))
    decision = await rate_limit_allow(
        redis,
        ip_key,
        rate_per_second=settings.sandbox_creation_rate_limit_requests_per_second,
        burst=settings.sandbox_creation_rate_limit_burst,
    )
    if not decision.allowed:
        raise RateLimitExceeded(decision.retry_after_seconds)

    result = await SandboxService(uow, settings=settings).provision()
    return SandboxCreated.from_result(result)


async def _event_stream(redis: Redis, tenant_id: uuid.UUID) -> AsyncIterator[str]:
    async for raw_json in _with_heartbeat(subscribe_attempt_events(redis, tenant_id)):
        yield raw_json


async def _with_heartbeat(source: AsyncIterator[str]) -> AsyncIterator[str]:
    """Wraps a raw async iterator of SSE `data:` payloads, interleaving a `: keep-alive`
    comment line whenever nothing has arrived for `_SSE_HEARTBEAT_SECONDS`. Framed as
    complete SSE lines here (not left to the caller) so this is the one place that owns
    the wire format.
    """
    iterator = source.__aiter__()
    pending: asyncio.Task[str] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=_SSE_HEARTBEAT_SECONDS)
            if pending in done:
                try:
                    payload = pending.result()
                except StopAsyncIteration:
                    return
                pending = None
                yield f"data: {payload}\n\n"
            else:
                yield ": keep-alive\n\n"
    finally:
        if pending is not None:
            pending.cancel()


@router.get(
    "/stream",
    responses=problem_responses(401, 403),
    summary="Live delivery-attempt timeline (SSE)",
    description=(
        "Server-Sent Events stream of the authenticated tenant's delivery attempts as "
        "they happen -- attempt #, status, latency, error class, next_retry_at, and "
        "breaker state. Scoped by construction: this subscribes to a per-tenant Redis "
        "Pub/Sub channel, so another tenant's events are never received, not merely "
        "filtered out."
    ),
)
async def stream_attempts(
    auth: AuthContext = Depends(_require_read_stream),
    redis: Redis = Depends(get_redis),
) -> StreamingResponse:
    return StreamingResponse(
        _event_stream(redis, auth.tenant.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/metrics",
    response_model=MetricsRead,
    responses=problem_responses(401, 403),
    summary="Live delivery metrics for the console's metrics strip",
    description=(
        "Queue depth, in-flight count, p95 latency, and success rate over a bounded "
        "recent sample of the tenant's delivery attempts -- computed on request from "
        "existing tables, not a standing Prometheus/Grafana stack (that's optional "
        "Phase 5)."
    ),
)
async def get_metrics(
    auth: AuthContext = Depends(_require_read),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> MetricsRead:
    metrics = await MetricsService(uow).snapshot(auth.tenant.id)
    return MetricsRead.from_domain(metrics)


@router.post(
    "/verify-signature",
    response_model=SignatureVerifyResponse,
    responses=problem_responses(401, 403, 422),
    summary="Verify an HMAC delivery signature",
    description=(
        "Thin wrapper over relay.infra.signing.verify() -- the exact function every "
        "receiver should run on their end -- so the console's signature-verification "
        "widget exercises the real implementation rather than a JS reimplementation of it."
    ),
)
async def verify_signature(
    body: SignatureVerifyRequest,
    auth: AuthContext = Depends(_require_read),
    settings: Settings = Depends(get_settings),
) -> SignatureVerifyResponse:
    tolerance = (
        body.tolerance_seconds
        if body.tolerance_seconds is not None
        else settings.signature_tolerance_seconds
    )
    valid = verify(
        body.secret,
        body.timestamp,
        body.body.encode(),
        body.signature,
        tolerance_seconds=tolerance,
    )
    return SignatureVerifyResponse(valid=valid)
