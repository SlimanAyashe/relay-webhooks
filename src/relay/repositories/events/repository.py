import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from relay.domain.errors import ConflictError
from relay.domain.events import Event
from relay.repositories.events.models import EventModel

_UNIQUE_VIOLATION_SQLSTATE = "23505"


class IdempotencyKeyConflict(ConflictError):
    """A row already exists for (tenant_id, idempotency_key). Raised only on the DB-level
    unique-violation race — the expected path is the service layer checking
    get_by_idempotency_key() first and deciding same-body-replay vs differing-body-409
    itself; this is the concurrent-double-submit fallback.
    """

    def __init__(self, tenant_id: uuid.UUID, idempotency_key: str) -> None:
        self.tenant_id = tenant_id
        self.idempotency_key = idempotency_key
        super().__init__(f"idempotency key conflict for tenant {tenant_id!r}: {idempotency_key!r}")


def _to_domain(model: EventModel) -> Event:
    return Event(
        id=model.id,
        tenant_id=model.tenant_id,
        type=model.type,
        payload=model.payload,
        idempotency_key=model.idempotency_key,
        created_at=model.created_at,
        correlation_id=model.correlation_id,
    )


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        tenant_id: uuid.UUID,
        event_type: str,
        payload: dict[str, object],
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> Event:
        model = EventModel(
            tenant_id=tenant_id,
            type=event_type,
            payload=payload,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(model)
                await self._session.flush()
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == _UNIQUE_VIOLATION_SQLSTATE:
                raise IdempotencyKeyConflict(
                    tenant_id=tenant_id, idempotency_key=idempotency_key
                ) from exc
            raise
        await self._session.refresh(model)
        return _to_domain(model)

    async def get(self, event_id: uuid.UUID) -> Event | None:
        model = await self._session.get(EventModel, event_id)
        return _to_domain(model) if model is not None else None

    async def count_for_tenant(self, tenant_id: uuid.UUID) -> int:
        """Used by the Phase 4 sandbox quota check (relay.services.sandbox.service)."""
        result = await self._session.execute(
            select(func.count()).select_from(EventModel).where(EventModel.tenant_id == tenant_id)
        )
        return result.scalar_one()

    async def get_by_idempotency_key(
        self, tenant_id: uuid.UUID, idempotency_key: str
    ) -> Event | None:
        result = await self._session.execute(
            select(EventModel).where(
                EventModel.tenant_id == tenant_id,
                EventModel.idempotency_key == idempotency_key,
            )
        )
        model = result.scalar_one_or_none()
        return _to_domain(model) if model is not None else None
