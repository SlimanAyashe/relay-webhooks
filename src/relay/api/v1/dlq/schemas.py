import uuid
from datetime import datetime

from pydantic import BaseModel

from relay.domain.deliveries import DeliveryState
from relay.domain.delivery_attempts import AttemptErrorClass, DeliveryAttempt
from relay.services.deliveries.dlq_service import DeadDeliveryDetail


class DeliveryAttemptRead(BaseModel):
    id: uuid.UUID
    attempt_no: int
    latency_ms: int
    response_status: int | None
    error_class: AttemptErrorClass | None
    request_snippet: str | None
    response_snippet: str | None
    created_at: datetime

    @classmethod
    def from_domain(cls, attempt: DeliveryAttempt) -> "DeliveryAttemptRead":
        return cls(
            id=attempt.id,
            attempt_no=attempt.attempt_no,
            latency_ms=attempt.latency_ms,
            response_status=attempt.response_status,
            error_class=attempt.error_class,
            request_snippet=attempt.request_snippet,
            response_snippet=attempt.response_snippet,
            created_at=attempt.created_at,
        )


class DeadDeliveryRead(BaseModel):
    """A dead delivery (retry budget exhausted) with its full attempt history attached,
    so a caller doesn't need a second request per delivery to see why it died.
    """

    id: uuid.UUID
    event_id: uuid.UUID
    endpoint_id: uuid.UUID
    state: DeliveryState
    attempt_count: int
    created_at: datetime
    attempts: list[DeliveryAttemptRead]

    @classmethod
    def from_detail(cls, detail: DeadDeliveryDetail) -> "DeadDeliveryRead":
        return cls(
            id=detail.delivery.id,
            event_id=detail.delivery.event_id,
            endpoint_id=detail.delivery.endpoint_id,
            state=detail.delivery.state,
            attempt_count=detail.delivery.attempt_count,
            created_at=detail.delivery.created_at,
            attempts=[DeliveryAttemptRead.from_domain(a) for a in detail.attempts],
        )


class DlqListResponse(BaseModel):
    items: list[DeadDeliveryRead]
    next_cursor: str | None
    has_more: bool
