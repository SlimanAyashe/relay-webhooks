import uuid
from dataclasses import dataclass
from datetime import datetime

WILDCARD_SCOPE = "*"


@dataclass(frozen=True, slots=True)
class ApiKey:
    id: uuid.UUID
    tenant_id: uuid.UUID
    key_hash: str
    key_prefix: str
    scopes: frozenset[str]
    created_at: datetime
    revoked_at: datetime | None = None

    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def has_scope(self, required: str) -> bool:
        return WILDCARD_SCOPE in self.scopes or required in self.scopes
