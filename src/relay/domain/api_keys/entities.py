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
    # Set only for Phase-4 sandbox keys (POST /v1/sandbox) -- a normal tenant's key has
    # no expiry beyond explicit revocation.
    expires_at: datetime | None = None

    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at is not None and now >= self.expires_at

    def has_scope(self, required: str) -> bool:
        return WILDCARD_SCOPE in self.scopes or required in self.scopes
