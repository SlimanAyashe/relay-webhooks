import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from relay.domain.endpoints import BreakerState, Endpoint, EndpointStatus


class EndpointCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    subscribed_event_types: list[str] = Field(min_length=1)


class EndpointUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl | None = None
    subscribed_event_types: list[str] | None = Field(default=None, min_length=1)
    status: EndpointStatus | None = None


class EndpointRead(BaseModel):
    """Never includes `secret` — it's only returned once, at creation, via EndpointCreated."""

    id: uuid.UUID
    url: str
    subscribed_event_types: list[str]
    status: EndpointStatus
    breaker_state: BreakerState
    created_at: datetime

    @classmethod
    def from_domain(cls, endpoint: Endpoint) -> "EndpointRead":
        return cls(
            id=endpoint.id,
            url=endpoint.url,
            subscribed_event_types=sorted(endpoint.subscribed_event_types),
            status=endpoint.status,
            breaker_state=endpoint.breaker_state,
            created_at=endpoint.created_at,
        )


class EndpointCreated(EndpointRead):
    """Returned exactly once, at creation — the only time the HMAC signing secret is
    ever available. Never returned by a read/list endpoint.
    """

    secret: str

    @classmethod
    def created_from_domain(cls, endpoint: Endpoint) -> "EndpointCreated":
        return cls(
            id=endpoint.id,
            url=endpoint.url,
            subscribed_event_types=sorted(endpoint.subscribed_event_types),
            status=endpoint.status,
            breaker_state=endpoint.breaker_state,
            created_at=endpoint.created_at,
            secret=endpoint.secret,
        )


class EndpointListResponse(BaseModel):
    items: list[EndpointRead]
    next_cursor: str | None
    has_more: bool
