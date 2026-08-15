import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class AttemptErrorClass(StrEnum):
    """Populated only on a non-success attempt; `None` on the domain entity means the
    attempt succeeded (2xx) -- kept as an enum rather than a free-text column so a
    downstream consumer of delivery_attempts (a dashboard, a query) can rely on a closed
    set of values instead of parsing arbitrary exception strings.
    """

    HTTP_ERROR = "http_error"
    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"


@dataclass(frozen=True, slots=True)
class DeliveryAttempt:
    id: uuid.UUID
    delivery_id: uuid.UUID
    attempt_no: int
    latency_ms: int
    created_at: datetime
    response_status: int | None = None
    error_class: AttemptErrorClass | None = None
    request_snippet: str | None = None
    response_snippet: str | None = None
