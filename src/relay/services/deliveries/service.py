import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from relay.domain.deliveries import Delivery, compute_backoff_seconds
from relay.domain.endpoints import BreakerState, next_breaker_state, should_skip_for_open_breaker
from relay.domain.errors import NotFoundError
from relay.infra.http_sender import OutboundHttpSender
from relay.infra.settings import Settings, get_settings
from relay.infra.signing import sign
from relay.repositories.unit_of_work import UnitOfWork

REQUEST_SNIPPET_MAX_LEN = 2048


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DeliveryAttemptService:
    """Sends one delivery attempt over HTTP, classifies the outcome, and records it -- the
    single place that decides whether a Delivery moves to `delivered`, `retrying`, or
    (retry budget exhausted) `dead`, and where the endpoint's circuit breaker transitions.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        http_sender: OutboundHttpSender,
        *,
        clock: Callable[[], datetime] = _utcnow,
        settings: Settings | None = None,
    ) -> None:
        self._uow = uow
        self._http_sender = http_sender
        self._clock = clock
        self._settings = settings or get_settings()

    async def attempt(self, delivery_id: uuid.UUID) -> Delivery:
        async with self._uow:
            delivery = await self._uow.deliveries.get(delivery_id)
            if delivery is None:
                raise NotFoundError(f"delivery not found: {delivery_id}")
            event = await self._uow.events.get(delivery.event_id)
            if event is None:
                raise NotFoundError(f"event not found: {delivery.event_id}")
            endpoint = await self._uow.endpoints.get(delivery.endpoint_id)
            if endpoint is None:
                raise NotFoundError(f"endpoint not found: {delivery.endpoint_id}")

            now = self._clock()
            cooldown = timedelta(seconds=self._settings.breaker_cooldown_seconds)

            if should_skip_for_open_breaker(
                state=endpoint.breaker_state,
                opened_at=endpoint.opened_at,
                now=now,
                cooldown=cooldown,
            ):
                # The breaker is open and hasn't cooled down: don't extend the outage by
                # hitting a destination we already know is failing. Deferred, not counted
                # as an attempt -- see DeliveryRepository.reschedule.
                assert endpoint.opened_at is not None
                delivery = await self._uow.deliveries.reschedule(
                    delivery.id, next_retry_at=endpoint.opened_at + cooldown
                )
                await self._uow.commit()
                return delivery

            if endpoint.breaker_state is BreakerState.OPEN:
                # Cooldown elapsed: allow exactly one probe attempt by flipping to
                # half-open *before* sending, so the outcome below is evaluated against
                # HALF_OPEN's transition rules (closed on success, reopen on failure).
                endpoint = await self._uow.endpoints.set_breaker_state(
                    endpoint.id, breaker_state=BreakerState.HALF_OPEN, opened_at=endpoint.opened_at
                )

            payload = json.dumps(event.payload).encode()
            timestamp = int(now.timestamp())
            signature = sign(endpoint.secret, timestamp, payload)
            headers = {
                "Content-Type": "application/json",
                "X-Relay-Signature": signature,
                "X-Relay-Timestamp": str(timestamp),
                "X-Relay-Delivery-Id": str(delivery.id),
            }
            result = await self._http_sender.send(
                url=endpoint.url, payload=payload, headers=headers
            )

            transition = next_breaker_state(
                current_state=endpoint.breaker_state,
                consecutive_failures=endpoint.consecutive_failures,
                now=now,
                success=result.succeeded,
                failure_threshold=self._settings.breaker_failure_threshold,
            )
            await self._uow.endpoints.record_delivery_outcome(
                endpoint.id,
                breaker_state=transition.state,
                consecutive_failures=transition.consecutive_failures,
                opened_at=transition.opened_at,
            )

            attempt_no = delivery.attempt_count + 1
            await self._uow.delivery_attempts.create(
                delivery_id=delivery.id,
                attempt_no=attempt_no,
                latency_ms=result.latency_ms,
                response_status=result.status_code,
                error_class=result.error_class,
                request_snippet=payload[:REQUEST_SNIPPET_MAX_LEN].decode(errors="replace"),
                response_snippet=result.response_snippet,
            )

            if result.succeeded:
                delivery = await self._uow.deliveries.mark_delivered(delivery.id)
            elif attempt_no >= self._settings.delivery_max_attempts:
                delivery = await self._uow.deliveries.mark_dead(delivery.id)
            else:
                next_retry_at = now + timedelta(
                    seconds=compute_backoff_seconds(delivery.attempt_count)
                )
                delivery = await self._uow.deliveries.mark_retrying(
                    delivery.id, next_retry_at=next_retry_at
                )

            await self._uow.commit()
            return delivery
