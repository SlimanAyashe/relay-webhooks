import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Event:
    id: uuid.UUID
    tenant_id: uuid.UUID
    type: str
    payload: dict[str, object]
    idempotency_key: str
    created_at: datetime
    # The ingest request's trace/correlation id (relay.api.middleware.TraceIdMiddleware),
    # carried forward so every worker log line produced while delivering this event -- across
    # every retry -- can be bound to the same id the original request logged and returned as
    # X-Trace-Id. None for events ingested before this column existed.
    correlation_id: str | None = None
