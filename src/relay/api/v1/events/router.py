from fastapi import APIRouter, Depends, Header, Request, Response, status
from redis.asyncio import Redis

from relay.api.auth import AuthContext, require_scope
from relay.api.openapi import problem_responses
from relay.api.v1.events.schemas import EventCreate, EventRead
from relay.domain.errors import PayloadTooLarge, RateLimitExceeded
from relay.infra.rate_limit import allow as rate_limit_allow
from relay.infra.redis import get_redis
from relay.infra.settings import Settings, get_settings
from relay.repositories.unit_of_work import UnitOfWork, get_unit_of_work
from relay.services.events.service import EventIngestService
from relay.services.sandbox.service import SandboxService

router = APIRouter(prefix="/v1/events", tags=["events"])

_require_write = require_scope("events:write")


async def _rate_limited_auth(
    auth: AuthContext = Depends(_require_write),
    uow: UnitOfWork = Depends(get_unit_of_work),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    """Runs after authentication (so the token bucket is keyed on the real tenant, not an
    unauthenticated caller) and before the ingest service, enforcing the tenant's per-second
    token-bucket budget -- tighter for a sandbox tenant (Settings.sandbox_rate_limit_*)
    than a normal one, via the same token-bucket mechanism, just parameterized differently.
    A sandbox tenant's fixed max-event-count quota is also enforced here. A rejection
    raises RateLimitExceeded/SandboxQuotaExceeded, mapped centrally by relay.api.errors to
    429/403 -- ingest_event itself never touches either.
    """
    rate = (
        settings.sandbox_rate_limit_requests_per_second
        if auth.tenant.is_sandbox
        else (settings.rate_limit_requests_per_second)
    )
    burst = (
        settings.sandbox_rate_limit_burst if auth.tenant.is_sandbox else settings.rate_limit_burst
    )
    decision = await rate_limit_allow(redis, auth.tenant.id, rate_per_second=rate, burst=burst)
    if not decision.allowed:
        raise RateLimitExceeded(decision.retry_after_seconds)

    await SandboxService(uow, settings=settings).assert_event_quota(auth.tenant)
    return auth


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EventRead,
    responses=problem_responses(401, 403, 409, 413, 422, 429),
    summary="Ingest an event",
    description=(
        "Accepts an event for delivery to every endpoint subscribed to its type. "
        "Idempotency-Key is required: the same key with an identical body replays the "
        "original event (same 202, same event back); with a differing body it's a 409. "
        "Subject to a per-tenant rate limit; exceeding it returns 429 with Retry-After. "
        "The raw request body is capped at Settings.event_payload_max_bytes (413 over)."
    ),
)
async def ingest_event(
    request: Request,
    body: EventCreate,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        description="A client-generated key unique per logical event, for safe retries.",
        examples=["6c76a0e0-3b8e-4f8a-9b1a-2e6f9c9d2b7a"],
    ),
    auth: AuthContext = Depends(_rate_limited_auth),
    uow: UnitOfWork = Depends(get_unit_of_work),
    settings: Settings = Depends(get_settings),
) -> EventRead:
    """202, not 200 -- ingestion accepts the event for delivery, it doesn't confirm
    delivery. A replayed request (same Idempotency-Key, identical body) gets the same
    202 and the original event back, not a fresh one. A differing body raises
    DifferingBodyConflict, caught by the central error handler and turned into 409.
    """
    raw_body = await request.body()
    if len(raw_body) > settings.event_payload_max_bytes:
        raise PayloadTooLarge(len(raw_body), settings.event_payload_max_bytes)

    correlation_id = getattr(request.state, "trace_id", None)
    service = EventIngestService(uow)
    event = await service.ingest(
        auth.tenant.id, body.type, body.payload, idempotency_key, correlation_id
    )

    response.headers["Location"] = f"/v1/events/{event.id}"
    return EventRead.from_domain(event)
