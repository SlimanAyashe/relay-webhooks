import uuid
from datetime import datetime

from pydantic import BaseModel

from relay.domain.deliveries import Delivery, DeliveryState


class DeliveryRead(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    endpoint_id: uuid.UUID
    state: DeliveryState
    attempt_count: int
    next_retry_at: datetime | None
    created_at: datetime

    @classmethod
    def from_domain(cls, delivery: Delivery) -> "DeliveryRead":
        return cls(
            id=delivery.id,
            event_id=delivery.event_id,
            endpoint_id=delivery.endpoint_id,
            state=delivery.state,
            attempt_count=delivery.attempt_count,
            next_retry_at=delivery.next_retry_at,
            created_at=delivery.created_at,
        )
