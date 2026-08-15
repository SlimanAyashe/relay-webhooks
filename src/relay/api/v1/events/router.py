from fastapi import APIRouter, Depends, Header, HTTPException, Response, status

from relay.api.auth import AuthContext, require_scope
from relay.api.v1.events.schemas import EventCreate, EventRead
from relay.repositories.unit_of_work import UnitOfWork, get_unit_of_work
from relay.services.events.service import DifferingBodyConflict, EventIngestService

router = APIRouter(prefix="/v1/events", tags=["events"])

_require_write = require_scope("events:write")


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=EventRead)
async def ingest_event(
    body: EventCreate,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    auth: AuthContext = Depends(_require_write),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> EventRead:
    """202, not 200 -- ingestion accepts the event for delivery, it doesn't confirm
    delivery. A replayed request (same Idempotency-Key, identical body) gets the same
    202 and the original event back, not a fresh one.
    """
    service = EventIngestService(uow)
    try:
        event = await service.ingest(auth.tenant.id, body.type, body.payload, idempotency_key)
    except DifferingBodyConflict as exc:
        # TODO(p1-22/p1-23): translate into the shared RFC 9457 problem+json envelope
        # once the central exception hierarchy exists; plain HTTPException for now.
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    response.headers["Location"] = f"/v1/events/{event.id}"
    return EventRead.from_domain(event)
