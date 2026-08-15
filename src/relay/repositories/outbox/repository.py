import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from relay.domain.errors import NotFoundError
from relay.domain.outbox import OutboxEntry, OutboxStatus
from relay.repositories.outbox.models import OutboxModel

DEFAULT_CLAIM_LIMIT = 50


def _to_domain(model: OutboxModel) -> OutboxEntry:
    return OutboxEntry(
        id=model.id,
        event_id=model.event_id,
        status=OutboxStatus(model.status),
        attempts=model.attempts,
        created_at=model.created_at,
        locked_at=model.locked_at,
    )


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, event_id: uuid.UUID) -> OutboxEntry:
        model = OutboxModel(event_id=event_id)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_domain(model)

    async def claim_due(self, limit: int = DEFAULT_CLAIM_LIMIT) -> list[OutboxEntry]:
        """Claims up to `limit` pending rows via `SELECT ... FOR UPDATE SKIP LOCKED`, oldest
        first. The row lock lives inside the caller's transaction: if two relay instances
        call this concurrently, Postgres hands each row to exactly one of them and the other
        simply doesn't see it, rather than blocking or erroring. If the caller's transaction
        never commits (crash, or `mark_processed` never runs), the lock -- and the row's
        `pending` status -- is released automatically, so a fresh claim picks it up again.
        """
        result = await self._session.execute(
            select(OutboxModel)
            .where(OutboxModel.status == OutboxStatus.PENDING.value)
            .order_by(OutboxModel.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list(result.scalars())
        now = datetime.now(UTC)
        for row in rows:
            row.locked_at = now
            row.attempts += 1
        await self._session.flush()
        return [_to_domain(row) for row in rows]

    async def mark_processed(self, outbox_id: uuid.UUID) -> None:
        model = await self._session.get(OutboxModel, outbox_id)
        if model is None:
            raise NotFoundError(f"outbox entry not found: {outbox_id}")
        model.status = OutboxStatus.PROCESSED.value
        await self._session.flush()
