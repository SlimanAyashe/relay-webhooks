import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Event:
    id: uuid.UUID
    tenant_id: uuid.UUID
    type: str
    payload: dict[str, object]
    idempotency_key: str
    created_at: datetime
