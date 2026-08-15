import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from relay.domain.deliveries import Delivery, DeliveryState
from relay.domain.errors import NotFoundError
from relay.repositories.deliveries.models import DeliveryModel


def _to_domain(model: DeliveryModel) -> Delivery:
    return Delivery(
        id=model.id,
        event_id=model.event_id,
        endpoint_id=model.endpoint_id,
        state=DeliveryState(model.state),
        attempt_count=model.attempt_count,
        created_at=model.created_at,
        next_retry_at=model.next_retry_at,
    )


class DeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, event_id: uuid.UUID, endpoint_id: uuid.UUID) -> Delivery:
        model = DeliveryModel(event_id=event_id, endpoint_id=endpoint_id)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_domain(model)

    async def get(self, delivery_id: uuid.UUID) -> Delivery | None:
        model = await self._session.get(DeliveryModel, delivery_id)
        return _to_domain(model) if model is not None else None

    async def mark_delivered(self, delivery_id: uuid.UUID) -> Delivery:
        model = await self._get_or_raise(delivery_id)
        model.state = DeliveryState.DELIVERED.value
        model.attempt_count += 1
        model.next_retry_at = None
        await self._session.flush()
        await self._session.refresh(model)
        return _to_domain(model)

    async def mark_retrying(self, delivery_id: uuid.UUID, *, next_retry_at: datetime) -> Delivery:
        model = await self._get_or_raise(delivery_id)
        model.state = DeliveryState.RETRYING.value
        model.attempt_count += 1
        model.next_retry_at = next_retry_at
        await self._session.flush()
        await self._session.refresh(model)
        return _to_domain(model)

    async def _get_or_raise(self, delivery_id: uuid.UUID) -> DeliveryModel:
        model = await self._session.get(DeliveryModel, delivery_id)
        if model is None:
            raise NotFoundError(f"delivery not found: {delivery_id}")
        return model
