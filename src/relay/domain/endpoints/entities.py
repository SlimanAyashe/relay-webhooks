import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class EndpointStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class Endpoint:
    id: uuid.UUID
    tenant_id: uuid.UUID
    url: str
    secret: str
    subscribed_event_types: frozenset[str]
    created_at: datetime
    status: EndpointStatus = EndpointStatus.ACTIVE
    breaker_state: BreakerState = BreakerState.CLOSED
    opened_at: datetime | None = None

    def is_active(self) -> bool:
        return self.status is EndpointStatus.ACTIVE

    def is_subscribed_to(self, event_type: str) -> bool:
        return event_type in self.subscribed_event_types
