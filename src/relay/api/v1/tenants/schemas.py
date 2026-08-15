import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from relay.domain.tenants import Tenant


class TenantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


class TenantRead(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    deleted_at: datetime | None

    @classmethod
    def from_domain(cls, tenant: Tenant) -> "TenantRead":
        return cls(
            id=tenant.id,
            name=tenant.name,
            created_at=tenant.created_at,
            deleted_at=tenant.deleted_at,
        )
