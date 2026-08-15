import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSED = "processed"


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    id: uuid.UUID
    event_id: uuid.UUID
    status: OutboxStatus
    attempts: int
    created_at: datetime
    locked_at: datetime | None = None
