import uuid
from datetime import datetime

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from relay.domain.deliveries import Delivery, DeliveryState
from relay.domain.errors import NotFoundError
from relay.repositories.deliveries.models import DeliveryModel
from relay.repositories.events.models import EventModel
from relay.repositories.pagination import Page, decode_cursor, encode_cursor

DEFAULT_PAGE_LIMIT = 50


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

    async def mark_dead(self, delivery_id: uuid.UUID) -> Delivery:
        """Retry budget exhausted: the delivery lands in the DLQ. `deliveries WHERE
        state='dead'` *is* the DLQ per the plan's data model -- there's no separate table.
        """
        model = await self._get_or_raise(delivery_id)
        model.state = DeliveryState.DEAD.value
        model.attempt_count += 1
        model.next_retry_at = None
        await self._session.flush()
        await self._session.refresh(model)
        return _to_domain(model)

    async def reschedule(self, delivery_id: uuid.UUID, *, next_retry_at: datetime) -> Delivery:
        """Defers a delivery without recording it as an attempt: used when the circuit
        breaker is OPEN and the HTTP call is skipped entirely, so a breaker-open deferral
        doesn't consume any of the delivery's retry-attempt budget the way a real failed
        attempt (mark_retrying) does.
        """
        model = await self._get_or_raise(delivery_id)
        model.state = DeliveryState.RETRYING.value
        model.next_retry_at = next_retry_at
        await self._session.flush()
        await self._session.refresh(model)
        return _to_domain(model)

    async def reset_for_replay(self, delivery_id: uuid.UUID) -> Delivery:
        """Starts a fresh delivery chain after a DLQ replay: state -> pending,
        attempt_count and next_retry_at reset so the delivery gets a full retry budget
        again. delivery_attempts is a separate, append-only table and is never touched
        here -- replay only ever adds new rows after this point, so a fresh chain's
        attempt_no restarts from 1 while the exhausted chain's rows stay intact, queryable
        by their own (earlier) created_at.
        """
        model = await self._get_or_raise(delivery_id)
        model.state = DeliveryState.PENDING.value
        model.attempt_count = 0
        model.next_retry_at = None
        await self._session.flush()
        await self._session.refresh(model)
        return _to_domain(model)

    async def count_by_states_for_tenant(
        self, tenant_id: uuid.UUID, states: list[DeliveryState]
    ) -> int:
        """Used by the Phase 4 sandbox metrics snapshot (relay.services.deliveries.metrics_service)
        for queue depth / in-flight counts, scoped to `tenant_id` via the same join through
        `events` list_dead() uses.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(DeliveryModel)
            .join(EventModel, DeliveryModel.event_id == EventModel.id)
            .where(
                EventModel.tenant_id == tenant_id,
                DeliveryModel.state.in_([s.value for s in states]),
            )
        )
        return result.scalar_one()

    async def list_dead(
        self, tenant_id: uuid.UUID, *, cursor: str | None = None, limit: int = DEFAULT_PAGE_LIMIT
    ) -> Page[Delivery]:
        """Keyset-paginated `deliveries WHERE state='dead'`, scoped to `tenant_id` via a
        join through `events` -- deliveries has no tenant_id column of its own (only
        event_id/endpoint_id), per the plan's data model.
        """
        query = (
            select(DeliveryModel)
            .join(EventModel, DeliveryModel.event_id == EventModel.id)
            .where(
                EventModel.tenant_id == tenant_id,
                DeliveryModel.state == DeliveryState.DEAD.value,
            )
            .order_by(DeliveryModel.created_at, DeliveryModel.id)
            .limit(limit + 1)
        )
        if cursor is not None:
            cursor_created_at, cursor_id = decode_cursor(cursor)
            query = query.where(
                tuple_(DeliveryModel.created_at, DeliveryModel.id) > (cursor_created_at, cursor_id)
            )

        result = await self._session.execute(query)
        rows = list(result.scalars())

        has_more = len(rows) > limit
        page_rows = rows[:limit]
        next_cursor = (
            encode_cursor(page_rows[-1].created_at, page_rows[-1].id)
            if has_more and page_rows
            else None
        )
        return Page(
            items=[_to_domain(model) for model in page_rows],
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def _get_or_raise(self, delivery_id: uuid.UUID) -> DeliveryModel:
        model = await self._session.get(DeliveryModel, delivery_id)
        if model is None:
            raise NotFoundError(f"delivery not found: {delivery_id}")
        return model
