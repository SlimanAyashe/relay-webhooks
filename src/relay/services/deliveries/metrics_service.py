import uuid
from dataclasses import dataclass

from relay.domain.deliveries import DeliveryState
from relay.repositories.unit_of_work import UnitOfWork

DEFAULT_SAMPLE_SIZE = 200


@dataclass(frozen=True, slots=True)
class DeliveryMetrics:
    """A point-in-time snapshot for the demo console's metrics strip -- computed on
    request from existing repositories, not a standing Prometheus/Grafana stack (that's
    Phase 5, optional). `p95_latency_ms`/`success_rate` are None when there's no attempt
    history yet to compute them from.
    """

    queue_depth: int
    in_flight: int
    p95_latency_ms: int | None
    success_rate: float | None
    sample_size: int


def _percentile(sorted_values: list[int], fraction: float) -> int:
    """Nearest-rank percentile over an already-sorted list. Good enough for a live demo
    strip refreshed on a short poll -- not a claim of statistical rigor over a bounded,
    recency-biased sample (see recent_for_tenant's `limit`).
    """
    index = min(len(sorted_values) - 1, int(len(sorted_values) * fraction))
    return sorted_values[index]


class MetricsService:
    """Read-only: aggregates a tenant's current delivery state and a bounded recent
    attempt sample into the numbers the console's metrics strip displays.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def snapshot(
        self, tenant_id: uuid.UUID, *, sample_size: int = DEFAULT_SAMPLE_SIZE
    ) -> DeliveryMetrics:
        async with self._uow:
            queue_depth = await self._uow.deliveries.count_by_states_for_tenant(
                tenant_id, [DeliveryState.PENDING, DeliveryState.RETRYING]
            )
            # "In-flight" is approximated as deliveries currently in RETRYING state --
            # there's no live registry of attempts actually executing right now inside a
            # dispatcher worker, so this counts deliveries that have failed at least once
            # and are awaiting (or mid-) retry, not a literal concurrent-request count.
            in_flight = await self._uow.deliveries.count_by_states_for_tenant(
                tenant_id, [DeliveryState.RETRYING]
            )
            recent = await self._uow.delivery_attempts.recent_for_tenant(
                tenant_id, limit=sample_size
            )

        latencies = sorted(attempt.latency_ms for attempt in recent)
        p95 = _percentile(latencies, 0.95) if latencies else None

        successes = sum(
            1
            for attempt in recent
            if attempt.response_status is not None and 200 <= attempt.response_status < 300
        )
        success_rate = successes / len(recent) if recent else None

        return DeliveryMetrics(
            queue_depth=queue_depth,
            in_flight=in_flight,
            p95_latency_ms=p95,
            success_rate=success_rate,
            sample_size=len(recent),
        )
