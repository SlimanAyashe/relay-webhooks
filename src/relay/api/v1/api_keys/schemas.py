import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from relay.domain.api_keys import ApiKey


class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scopes: list[str] = Field(min_length=1)


class ApiKeyRead(BaseModel):
    """Key metadata only — key_hash never crosses the wire."""

    id: uuid.UUID
    key_prefix: str
    scopes: list[str]
    created_at: datetime
    revoked_at: datetime | None

    @classmethod
    def from_domain(cls, api_key: ApiKey) -> "ApiKeyRead":
        return cls(
            id=api_key.id,
            key_prefix=api_key.key_prefix,
            scopes=sorted(api_key.scopes),
            created_at=api_key.created_at,
            revoked_at=api_key.revoked_at,
        )


class ApiKeyIssued(ApiKeyRead):
    """Returned exactly once, at issuance or rotation — the only time the plaintext key
    is ever available. Never returned by a read/list endpoint.
    """

    key: str

    @classmethod
    def issued_from_domain(cls, api_key: ApiKey, plaintext_key: str) -> "ApiKeyIssued":
        return cls(
            id=api_key.id,
            key_prefix=api_key.key_prefix,
            scopes=sorted(api_key.scopes),
            created_at=api_key.created_at,
            revoked_at=api_key.revoked_at,
            key=plaintext_key,
        )
