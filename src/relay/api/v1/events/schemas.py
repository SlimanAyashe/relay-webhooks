import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from relay.domain.events import Event


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    payload: dict[str, object]


class EventRead(BaseModel):
    id: uuid.UUID
    type: str
    payload: dict[str, object]
    idempotency_key: str
    created_at: datetime

    @classmethod
    def from_domain(cls, event: Event) -> "EventRead":
        return cls(
            id=event.id,
            type=event.type,
            payload=event.payload,
            idempotency_key=event.idempotency_key,
            created_at=event.created_at,
        )
