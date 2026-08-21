import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from relay.domain.delivery_attempts import AttemptErrorClass, DeliveryAttempt
from relay.repositories.deliveries.models import DeliveryModel
from relay.repositories.delivery_attempts.models import DeliveryAttemptModel
from relay.repositories.events.models import EventModel


def _to_domain(model: DeliveryAttemptModel) -> DeliveryAttempt:
    return DeliveryAttempt(
        id=model.id,
        delivery_id=model.delivery_id,
        attempt_no=model.attempt_no,
        latency_ms=model.latency_ms,
        created_at=model.created_at,
        response_status=model.response_status,
        error_class=AttemptErrorClass(model.error_class) if model.error_class else None,
        request_snippet=model.request_snippet,
        response_snippet=model.response_snippet,
        request_headers=model.request_headers,
    )


class DeliveryAttemptRepository:
    """Append-only: attempts are an immutable log, so there is deliberately no update or
    delete operation here.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        delivery_id: uuid.UUID,
        attempt_no: int,
        latency_ms: int,
        *,
        response_status: int | None = None,
        error_class: AttemptErrorClass | None = None,
        request_snippet: str | None = None,
        response_snippet: str | None = None,
        request_headers: dict[str, str] | None = None,
    ) -> DeliveryAttempt:
        model = DeliveryAttemptModel(
            delivery_id=delivery_id,
            attempt_no=attempt_no,
            latency_ms=latency_ms,
            response_status=response_status,
            error_class=error_class.value if error_class is not None else None,
            request_snippet=request_snippet,
            response_snippet=response_snippet,
            request_headers=request_headers,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_domain(model)

    async def list_for_delivery(self, delivery_id: uuid.UUID) -> list[DeliveryAttempt]:
        """The full attempt history for one delivery, oldest first -- spans every replay
        chain (see DeliveryRepository.reset_for_replay), since attempts are never deleted
        or rewritten.
        """
        result = await self._session.execute(
            select(DeliveryAttemptModel)
            .where(DeliveryAttemptModel.delivery_id == delivery_id)
            .order_by(DeliveryAttemptModel.created_at, DeliveryAttemptModel.attempt_no)
        )
        return [_to_domain(model) for model in result.scalars()]

    async def recent_for_tenant(
        self, tenant_id: uuid.UUID, *, limit: int = 200
    ) -> list[DeliveryAttempt]:
        """The most recent attempts across every delivery/endpoint belonging to
        `tenant_id`, newest first -- the bounded sample the Phase 4 metrics snapshot
        (relay.services.deliveries.metrics_service) computes p95 latency and success
        rate from, rather than scanning the whole table.
        """
        result = await self._session.execute(
            select(DeliveryAttemptModel)
            .join(DeliveryModel, DeliveryAttemptModel.delivery_id == DeliveryModel.id)
            .join(EventModel, DeliveryModel.event_id == EventModel.id)
            .where(EventModel.tenant_id == tenant_id)
            .order_by(DeliveryAttemptModel.created_at.desc())
            .limit(limit)
        )
        return [_to_domain(model) for model in result.scalars()]
