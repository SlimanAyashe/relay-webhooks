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
