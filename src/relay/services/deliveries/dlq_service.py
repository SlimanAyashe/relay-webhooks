import uuid
from dataclasses import dataclass

from relay.domain.deliveries import Delivery
from relay.domain.delivery_attempts import DeliveryAttempt
from relay.repositories.pagination import Page
from relay.repositories.unit_of_work import UnitOfWork


@dataclass(frozen=True, slots=True)
class DeadDeliveryDetail:
    """A dead delivery plus its full attempt history -- the DLQ listing's unit of
    response, not a domain entity itself (delivery_attempts is queried per delivery, not
    stored denormalized anywhere).
    """

    delivery: Delivery
    attempts: list[DeliveryAttempt]


class DlqService:
    """Read-only: lists a tenant's dead deliveries (`deliveries WHERE state='dead'`, the
    DLQ per the plan's data model) with each one's attempt history attached.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def list_dead(
        self, tenant_id: uuid.UUID, *, cursor: str | None = None, limit: int = 50
    ) -> Page[DeadDeliveryDetail]:
        async with self._uow:
            page = await self._uow.deliveries.list_dead(tenant_id, cursor=cursor, limit=limit)
            details = [
                DeadDeliveryDetail(
                    delivery=delivery,
                    attempts=await self._uow.delivery_attempts.list_for_delivery(delivery.id),
                )
                for delivery in page.items
            ]
        return Page(items=details, next_cursor=page.next_cursor, has_more=page.has_more)
