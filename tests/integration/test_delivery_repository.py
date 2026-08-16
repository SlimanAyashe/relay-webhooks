import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from relay.domain.deliveries import DeliveryState
from relay.domain.errors import NotFoundError
from relay.repositories.deliveries.repository import DeliveryRepository
from relay.repositories.endpoints.repository import EndpointRepository
from relay.repositories.events.repository import EventRepository
from relay.repositories.tenants.repository import TenantRepository


async def _make_event_and_endpoint_ids(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    tenant = await TenantRepository(session).create(name="acme")
    event = await EventRepository(session).create(
        tenant_id=tenant.id, event_type="t", payload={}, idempotency_key=str(uuid.uuid4())
    )
    endpoint = await EndpointRepository(session).create(
        tenant_id=tenant.id,
        url="https://example.com/webhook",
        secret="s3cr3t",
        subscribed_event_types=frozenset({"t"}),
    )
    return event.id, endpoint.id


async def test_create_defaults_to_pending_with_no_attempts(db_session: AsyncSession) -> None:
    event_id, endpoint_id = await _make_event_and_endpoint_ids(db_session)

    delivery = await DeliveryRepository(db_session).create(
        event_id=event_id, endpoint_id=endpoint_id
    )

    assert delivery.state is DeliveryState.PENDING
    assert delivery.attempt_count == 0
    assert delivery.next_retry_at is None


async def test_mark_delivered_sets_terminal_state_and_increments_attempts(
    db_session: AsyncSession,
) -> None:
    event_id, endpoint_id = await _make_event_and_endpoint_ids(db_session)
    repo = DeliveryRepository(db_session)
    delivery = await repo.create(event_id=event_id, endpoint_id=endpoint_id)

    updated = await repo.mark_delivered(delivery.id)

    assert updated.state is DeliveryState.DELIVERED
    assert updated.attempt_count == 1
    assert updated.next_retry_at is None


async def test_mark_retrying_sets_next_retry_at_and_increments_attempts(
    db_session: AsyncSession,
) -> None:
    event_id, endpoint_id = await _make_event_and_endpoint_ids(db_session)
    repo = DeliveryRepository(db_session)
    delivery = await repo.create(event_id=event_id, endpoint_id=endpoint_id)
    due = datetime.now(UTC) + timedelta(seconds=30)

    updated = await repo.mark_retrying(delivery.id, next_retry_at=due)

    assert updated.state is DeliveryState.RETRYING
    assert updated.attempt_count == 1
    assert updated.next_retry_at == due


async def test_get_missing_delivery_returns_none(db_session: AsyncSession) -> None:
    assert await DeliveryRepository(db_session).get(uuid.uuid4()) is None


async def test_mark_delivered_missing_delivery_raises(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await DeliveryRepository(db_session).mark_delivered(uuid.uuid4())


async def test_mark_dead_sets_terminal_state_and_increments_attempts(
    db_session: AsyncSession,
) -> None:
    event_id, endpoint_id = await _make_event_and_endpoint_ids(db_session)
    repo = DeliveryRepository(db_session)
    delivery = await repo.create(event_id=event_id, endpoint_id=endpoint_id)

    updated = await repo.mark_dead(delivery.id)

    assert updated.state is DeliveryState.DEAD
    assert updated.attempt_count == 1
    assert updated.next_retry_at is None


async def test_reschedule_does_not_increment_attempt_count(db_session: AsyncSession) -> None:
    """Distinguishes a breaker-open deferral from a real failed attempt: reschedule()
    moves the delivery back to retrying without spending any of its retry-attempt budget.
    """
    event_id, endpoint_id = await _make_event_and_endpoint_ids(db_session)
    repo = DeliveryRepository(db_session)
    delivery = await repo.create(event_id=event_id, endpoint_id=endpoint_id)
    due = datetime.now(UTC) + timedelta(seconds=60)

    updated = await repo.reschedule(delivery.id, next_retry_at=due)

    assert updated.state is DeliveryState.RETRYING
    assert updated.attempt_count == 0
    assert updated.next_retry_at == due


async def test_reset_for_replay_starts_a_fresh_chain(db_session: AsyncSession) -> None:
    event_id, endpoint_id = await _make_event_and_endpoint_ids(db_session)
    repo = DeliveryRepository(db_session)
    delivery = await repo.create(event_id=event_id, endpoint_id=endpoint_id)
    await repo.mark_dead(delivery.id)

    replayed = await repo.reset_for_replay(delivery.id)

    assert replayed.state is DeliveryState.PENDING
    assert replayed.attempt_count == 0
    assert replayed.next_retry_at is None


async def test_list_dead_returns_only_dead_deliveries_scoped_to_tenant(
    db_session: AsyncSession,
) -> None:
    tenant_a = await TenantRepository(db_session).create(name="acme-a")
    tenant_b = await TenantRepository(db_session).create(name="acme-b")
    repo = DeliveryRepository(db_session)

    async def _make_dead(tenant_id: uuid.UUID) -> uuid.UUID:
        event = await EventRepository(db_session).create(
            tenant_id=tenant_id, event_type="t", payload={}, idempotency_key=str(uuid.uuid4())
        )
        endpoint = await EndpointRepository(db_session).create(
            tenant_id=tenant_id,
            url="https://example.com/webhook",
            secret="s3cr3t",
            subscribed_event_types=frozenset({"t"}),
        )
        delivery = await repo.create(event_id=event.id, endpoint_id=endpoint.id)
        await repo.mark_dead(delivery.id)
        return delivery.id

    async def _make_pending(tenant_id: uuid.UUID) -> None:
        event = await EventRepository(db_session).create(
            tenant_id=tenant_id, event_type="t", payload={}, idempotency_key=str(uuid.uuid4())
        )
        endpoint = await EndpointRepository(db_session).create(
            tenant_id=tenant_id,
            url="https://example.com/webhook",
            secret="s3cr3t",
            subscribed_event_types=frozenset({"t"}),
        )
        await repo.create(event_id=event.id, endpoint_id=endpoint.id)

    dead_a = await _make_dead(tenant_a.id)
    await _make_pending(tenant_a.id)  # not dead -- must not appear
    await _make_dead(tenant_b.id)  # different tenant -- must not appear

    page = await repo.list_dead(tenant_a.id)

    assert [d.id for d in page.items] == [dead_a]
    assert page.items[0].state is DeliveryState.DEAD


async def test_list_dead_paginates_with_no_skips_or_duplicates(db_session: AsyncSession) -> None:
    tenant = await TenantRepository(db_session).create(name="acme")
    endpoint = await EndpointRepository(db_session).create(
        tenant_id=tenant.id,
        url="https://example.com/webhook",
        secret="s3cr3t",
        subscribed_event_types=frozenset({"t"}),
    )
    repo = DeliveryRepository(db_session)
    dead_ids = []
    for _ in range(5):
        event = await EventRepository(db_session).create(
            tenant_id=tenant.id, event_type="t", payload={}, idempotency_key=str(uuid.uuid4())
        )
        delivery = await repo.create(event_id=event.id, endpoint_id=endpoint.id)
        await repo.mark_dead(delivery.id)
        dead_ids.append(delivery.id)

    seen_ids: list[uuid.UUID] = []
    cursor: str | None = None
    while True:
        page = await repo.list_dead(tenant.id, cursor=cursor, limit=2)
        seen_ids.extend(d.id for d in page.items)
        if not page.has_more:
            assert page.next_cursor is None
            break
        cursor = page.next_cursor

    assert len(seen_ids) == len(set(seen_ids)) == len(dead_ids)
    assert set(seen_ids) == set(dead_ids)
