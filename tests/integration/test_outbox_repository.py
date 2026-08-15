import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from relay.domain.errors import NotFoundError
from relay.domain.outbox import OutboxStatus
from relay.repositories.events.repository import EventRepository
from relay.repositories.outbox.repository import OutboxRepository
from relay.repositories.tenants.repository import TenantRepository


async def _make_event_id(session: AsyncSession) -> uuid.UUID:
    tenant = await TenantRepository(session).create(name="acme")
    event = await EventRepository(session).create(
        tenant_id=tenant.id, event_type="t", payload={}, idempotency_key=str(uuid.uuid4())
    )
    return event.id


async def test_create_defaults_to_pending_unlocked(db_session: AsyncSession) -> None:
    event_id = await _make_event_id(db_session)

    entry = await OutboxRepository(db_session).create(event_id=event_id)

    assert entry.status is OutboxStatus.PENDING
    assert entry.attempts == 0
    assert entry.locked_at is None


async def test_claim_due_returns_only_pending_rows_and_marks_them_locked(
    db_session: AsyncSession,
) -> None:
    event_id = await _make_event_id(db_session)
    repo = OutboxRepository(db_session)
    created = await repo.create(event_id=event_id)

    claimed = await repo.claim_due(limit=10)

    assert [entry.id for entry in claimed] == [created.id]
    assert claimed[0].locked_at is not None
    assert claimed[0].attempts == 1


async def test_claim_due_does_not_return_already_processed_rows(db_session: AsyncSession) -> None:
    event_id = await _make_event_id(db_session)
    repo = OutboxRepository(db_session)
    entry = await repo.create(event_id=event_id)
    await repo.mark_processed(entry.id)

    claimed = await repo.claim_due(limit=10)

    assert claimed == []


async def test_mark_processed_flips_status_so_it_is_no_longer_claimable(
    db_session: AsyncSession,
) -> None:
    event_id = await _make_event_id(db_session)
    repo = OutboxRepository(db_session)
    entry = await repo.create(event_id=event_id)

    await repo.mark_processed(entry.id)

    assert await repo.claim_due(limit=10) == []


async def test_mark_processed_missing_row_raises(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await OutboxRepository(db_session).mark_processed(uuid.uuid4())


async def test_skip_locked_gives_a_claimed_row_to_exactly_one_concurrent_claimer(
    db_engine: AsyncEngine,
) -> None:
    """Testing scenario #2: two relay instances racing SELECT ... FOR UPDATE SKIP LOCKED
    for the same due outbox row -- the row must go to exactly one of them, and the loser
    must see nothing rather than blocking or double-claiming.
    """
    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)
    async with sessionmaker() as setup_session:
        event_id = await _make_event_id(setup_session)
        await OutboxRepository(setup_session).create(event_id=event_id)
        await setup_session.commit()

    results: list[int] = []

    async def _claim_in_own_transaction() -> None:
        async with sessionmaker() as session, session.begin():
            claimed = await OutboxRepository(session).claim_due(limit=10)
            results.append(len(claimed))
            # Hold the row lock open for a moment so the other claimer's SKIP LOCKED
            # genuinely races against a live, uncommitted transaction rather than one
            # that already finished.
            await asyncio.sleep(0.2)

    await asyncio.gather(_claim_in_own_transaction(), _claim_in_own_transaction())

    assert sorted(results) == [0, 1]
