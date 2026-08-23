"""Prometheus collectors, as module-level singletons on the client library's default
registry -- the standard prometheus-client pattern. Each metric is recorded in the process
that actually observes it (see docs/adr/0007-phase-5-observability-and-ops.md for why that
means two scrape targets, api:/metrics and the dispatcher's own exporter, rather than one):

- `http_request_duration_seconds`: recorded by relay.api.middleware.TraceIdMiddleware,
  scraped from the api process's GET /metrics.
- `delivery_attempts_total` / `circuit_breaker_state`: recorded wherever the dispatcher
  (via relay.services.deliveries.service.DeliveryAttemptService and
  relay.repositories.endpoints.repository.EndpointRepository) observes an outcome or a
  breaker transition -- true regardless of whether the dispatcher or the reaper is the one
  driving that attempt, since both call through the same service.
- `delivery_queue_depth` / `delivery_in_flight`: sampled directly from Redis Streams
  (XLEN / XPENDING) by the dispatcher's own poll loop.
"""

from prometheus_client import Counter, Enum, Gauge, Histogram

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "API request latency in seconds",
    ["method", "path", "status_code"],
)

delivery_attempts_total = Counter(
    "delivery_attempts_total",
    "Delivery attempts by outcome",
    ["outcome"],
)

circuit_breaker_state = Enum(
    "circuit_breaker_state",
    "Per-endpoint circuit breaker state",
    ["endpoint_id"],
    states=["closed", "open", "half_open"],
)

delivery_queue_depth = Gauge(
    "delivery_queue_depth",
    "Entries on the delivery stream not yet delivered to any consumer",
)

delivery_in_flight = Gauge(
    "delivery_in_flight",
    "Delivery stream entries claimed by a consumer but not yet acked",
)


def attempt_outcome_label(*, succeeded: bool, exhausted: bool, error_class: str | None) -> str:
    """Maps one delivery attempt's result onto delivery_attempts_total's closed label set.
    Budget exhaustion takes priority over the specific error class -- an operator watching
    this counter cares most that a delivery just moved to the DLQ, not which error finally
    used up its last attempt.
    """
    if succeeded:
        return "success"
    if exhausted:
        return "exhausted"
    if error_class in ("ssrf_blocked", "timeout"):
        return error_class
    return "retry"
