import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class DeliveryState(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    RETRYING = "retrying"
    # Not reachable yet in Phase 2 -- Phase 3's circuit breaker and retry-budget
    # policy are what decide when a delivery gives up and lands here. The value
    # exists now because the plan's data model defines the DLQ as
    # `deliveries WHERE state='dead'`, not a separate table.
    DEAD = "dead"


@dataclass(frozen=True, slots=True)
class Delivery:
    id: uuid.UUID
    event_id: uuid.UUID
    endpoint_id: uuid.UUID
    state: DeliveryState
    attempt_count: int
    created_at: datetime
    next_retry_at: datetime | None = None
