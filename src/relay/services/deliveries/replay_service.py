import uuid

from redis.asyncio import Redis

from relay.domain.deliveries import Delivery, DeliveryState
from relay.domain.errors import ConflictError, NotFoundError
from relay.infra.streams import enqueue_delivery
from relay.repositories.unit_of_work import UnitOfWork


class DeliveryNotDeadError(ConflictError):
    """Only a delivery in DeliveryState.DEAD can be replayed -- one still pending/retrying
    already has a live chain, and a delivered one has nothing to retry.
    """

    def __init__(self, delivery_id: uuid.UUID, state: DeliveryState) -> None:
        self.delivery_id = delivery_id
        self.state = state
        super().__init__(
            f"delivery {delivery_id!r} is not dead (state={state.value!r}); cannot replay"
        )


class ReplayService:
    """Starts a fresh delivery chain for a dead delivery: resets it to `pending` (a full
    retry budget, no stale next_retry_at) and re-publishes it to the Redis delivery stream
    so the dispatcher picks it up again -- the original delivery_attempts history is never
    touched, so it stays queryable exactly as it was.
    """

    def __init__(self, uow: UnitOfWork, redis: Redis) -> None:
        self._uow = uow
        self._redis = redis

    async def replay(self, delivery_id: uuid.UUID, tenant_id: uuid.UUID) -> Delivery:
        async with self._uow:
            delivery = await self._uow.deliveries.get(delivery_id)
            if delivery is None:
                raise NotFoundError(f"delivery not found: {delivery_id}")

            event = await self._uow.events.get(delivery.event_id)
            if event is None or event.tenant_id != tenant_id:
                # 404, not 403 -- tenant A's key must not be able to tell "doesn't exist"
                # apart from "belongs to someone else", same isolation rule as endpoints.
                raise NotFoundError(f"delivery not found for tenant {tenant_id!r}: {delivery_id!r}")

            if delivery.state is not DeliveryState.DEAD:
                raise DeliveryNotDeadError(delivery_id, delivery.state)

            replayed = await self._uow.deliveries.reset_for_replay(delivery_id)
            await self._uow.commit()

        # Published after the commit, directly to the stream rather than through the
        # outbox -- see docs/adr/0005-phase-3-security-resilience.md for the residual
        # crash-window tradeoff this accepts (same shape as the relay worker's fan-out
        # publish, see docs/adr/0004-phase-2-delivery-engine.md).
        await enqueue_delivery(self._redis, replayed.id)
        return replayed
