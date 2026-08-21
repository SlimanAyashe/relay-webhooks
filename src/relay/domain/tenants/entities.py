import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Tenant:
    id: uuid.UUID
    name: str
    created_at: datetime
    deleted_at: datetime | None = None
    # True for a Phase-4 sandbox tenant self-provisioned via POST /v1/sandbox -- gates
    # the tighter sandbox quotas (relay.domain.sandbox.quota) on top of the normal
    # per-tenant rate limiter, which still applies to every tenant regardless.
    is_sandbox: bool = False

    def is_deleted(self) -> bool:
        return self.deleted_at is not None
