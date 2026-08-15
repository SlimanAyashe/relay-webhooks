import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from relay.domain.deliveries import Delivery, compute_backoff_seconds
from relay.domain.errors import NotFoundError
from relay.infra.http_sender import OutboundHttpSender
from relay.repositories.unit_of_work import UnitOfWork

REQUEST_SNIPPET_MAX_LEN = 2048


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DeliveryAttemptService:
    """Sends one delivery attempt over HTTP, classifies the outcome, and records it -- the
    single place that decides whether a Delivery moves to `delivered` or `retrying` and, if
    retrying, when the next attempt is due.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        http_sender: OutboundHttpSender,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._uow = uow
        self._http_sender = http_sender
        self._clock = clock

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

            payload = json.dumps(event.payload).encode()
            result = await self._http_sender.send(
                url=endpoint.url, payload=payload, headers={"Content-Type": "application/json"}
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
            else:
                next_retry_at = self._clock() + timedelta(
                    seconds=compute_backoff_seconds(delivery.attempt_count)
                )
                delivery = await self._uow.deliveries.mark_retrying(
                    delivery.id, next_retry_at=next_retry_at
                )

            await self._uow.commit()
            return delivery
