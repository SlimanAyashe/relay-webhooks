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
    # The destination (or a redirect hop) resolved to a denied network -- loopback,
    # RFC1918, link-local, CGNAT, IPv6 ULA, or the cloud metadata IP -- or targeted a
    # non-allow-listed port. Recorded as its own class rather than folded into
    # CONNECTION_ERROR so a blocked destination is distinguishable from a merely
    # unreachable one, both to an operator and to the demo console.
    SSRF_BLOCKED = "ssrf_blocked"


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
