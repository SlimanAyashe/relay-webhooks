import uuid

from sqlalchemy import any_, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from relay.domain.endpoints import BreakerState, Endpoint, EndpointStatus
from relay.domain.errors import NotFoundError
from relay.repositories.endpoints.models import EndpointModel
from relay.repositories.pagination import Page, decode_cursor, encode_cursor

DEFAULT_PAGE_LIMIT = 50


def _to_domain(model: EndpointModel) -> Endpoint:
    return Endpoint(
        id=model.id,
        tenant_id=model.tenant_id,
        url=model.url,
        secret=model.secret,
        subscribed_event_types=frozenset(model.subscribed_event_types),
        created_at=model.created_at,
        status=EndpointStatus(model.status),
        breaker_state=BreakerState(model.breaker_state),
        opened_at=model.opened_at,
    )


class EndpointRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, tenant_id: uuid.UUID, url: str, secret: str, subscribed_event_types: frozenset[str]
    ) -> Endpoint:
        model = EndpointModel(
            tenant_id=tenant_id,
            url=url,
            secret=secret,
            subscribed_event_types=list(subscribed_event_types),
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_domain(model)

    async def get(self, endpoint_id: uuid.UUID) -> Endpoint | None:
        model = await self._session.get(EndpointModel, endpoint_id)
        return _to_domain(model) if model is not None else None

    async def update(
        self,
        endpoint_id: uuid.UUID,
        *,
        url: str | None = None,
        subscribed_event_types: frozenset[str] | None = None,
        status: EndpointStatus | None = None,
    ) -> Endpoint:
        model = await self._session.get(EndpointModel, endpoint_id)
        if model is None:
            raise NotFoundError(f"endpoint not found: {endpoint_id}")
        if url is not None:
            model.url = url
        if subscribed_event_types is not None:
            model.subscribed_event_types = list(subscribed_event_types)
        if status is not None:
            model.status = status.value
        await self._session.flush()
        await self._session.refresh(model)
        return _to_domain(model)

    async def delete(self, endpoint_id: uuid.UUID) -> None:
        model = await self._session.get(EndpointModel, endpoint_id)
        if model is None:
            raise NotFoundError(f"endpoint not found: {endpoint_id}")
        await self._session.delete(model)
        await self._session.flush()

    async def list_active_subscribed(self, tenant_id: uuid.UUID, event_type: str) -> list[Endpoint]:
        """Active endpoints for `tenant_id` subscribed to `event_type` -- the fan-out target
        set the relay worker uses to turn one event into one Delivery per endpoint.
        """
        result = await self._session.execute(
            select(EndpointModel).where(
                EndpointModel.tenant_id == tenant_id,
                EndpointModel.status == EndpointStatus.ACTIVE.value,
                literal(event_type) == any_(EndpointModel.subscribed_event_types),
            )
        )
        return [_to_domain(model) for model in result.scalars()]

    async def list(
        self, tenant_id: uuid.UUID, *, cursor: str | None = None, limit: int = DEFAULT_PAGE_LIMIT
    ) -> Page[Endpoint]:
        """Keyset-paginates over (created_at, id) — a stable total order even when two
        rows share a created_at timestamp — rather than OFFSET, which drifts under
        concurrent writes and gets slower the deeper you page.
        """
        query = (
            select(EndpointModel)
            .where(EndpointModel.tenant_id == tenant_id)
            .order_by(EndpointModel.created_at, EndpointModel.id)
            .limit(limit + 1)
        )
        if cursor is not None:
            cursor_created_at, cursor_id = decode_cursor(cursor)
            query = query.where(
                tuple_(EndpointModel.created_at, EndpointModel.id) > (cursor_created_at, cursor_id)
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
