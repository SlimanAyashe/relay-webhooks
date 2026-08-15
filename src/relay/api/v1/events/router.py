from fastapi import APIRouter, Depends, Header, Response, status

from relay.api.auth import AuthContext, require_scope
from relay.api.openapi import problem_responses
from relay.api.v1.events.schemas import EventCreate, EventRead
from relay.repositories.unit_of_work import UnitOfWork, get_unit_of_work
from relay.services.events.service import EventIngestService

router = APIRouter(prefix="/v1/events", tags=["events"])

_require_write = require_scope("events:write")


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EventRead,
    responses=problem_responses(401, 403, 409, 422),
    summary="Ingest an event",
    description=(
        "Accepts an event for delivery to every endpoint subscribed to its type. "
        "Idempotency-Key is required: the same key with an identical body replays the "
        "original event (same 202, same event back); with a differing body it's a 409."
    ),
)
async def ingest_event(
    body: EventCreate,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        description="A client-generated key unique per logical event, for safe retries.",
        examples=["6c76a0e0-3b8e-4f8a-9b1a-2e6f9c9d2b7a"],
    ),
    auth: AuthContext = Depends(_require_write),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> EventRead:
    """202, not 200 -- ingestion accepts the event for delivery, it doesn't confirm
    delivery. A replayed request (same Idempotency-Key, identical body) gets the same
    202 and the original event back, not a fresh one. A differing body raises
    DifferingBodyConflict, caught by the central error handler and turned into 409.
    """
    service = EventIngestService(uow)
    event = await service.ingest(auth.tenant.id, body.type, body.payload, idempotency_key)

    response.headers["Location"] = f"/v1/events/{event.id}"
    return EventRead.from_domain(event)
