import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Tenant:
    id: uuid.UUID
    name: str
    created_at: datetime
    deleted_at: datetime | None = None

    def is_deleted(self) -> bool:
        return self.deleted_at is not None
