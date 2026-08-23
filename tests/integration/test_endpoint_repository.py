import uuid
from datetime import UTC, datetime

import pytest
from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import AsyncSession

from relay.domain.endpoints import BreakerState, EndpointStatus
from relay.domain.errors import NotFoundError
from relay.repositories.endpoints.repository import EndpointRepository
from relay.repositories.tenants.repository import TenantRepository

_BREAKER_STATES = ("closed", "open", "half_open")


async def _make_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    tenant = await TenantRepository(db_session).create(name="acme")
    return tenant.id


def _gauge_state(endpoint_id: uuid.UUID) -> str | None:
    """Reads circuit_breaker_state's current value for one endpoint back out of the
    default Prometheus registry. prometheus_client.Enum represents "which one of N states
    is active" as one 0/1 series per state, each carrying the state name as a label named
    after the metric itself -- so the active state is whichever one currently reads 1.0.
    None means the metric has never been written for this endpoint_id at all.
    """
    for state in _BREAKER_STATES:
        value = REGISTRY.get_sample_value(
            "circuit_breaker_state",
            {"endpoint_id": str(endpoint_id), "circuit_breaker_state": state},
        )
        if value == 1.0:
            return state
    return None


async def test_create_and_get_round_trip(db_session: AsyncSession) -> None:
    tenant_id = await _make_tenant_id(db_session)
    repo = EndpointRepository(db_session)

    created = await repo.create(
        tenant_id=tenant_id,
        url="https://example.com/hook",
        secret="s3cr3t",
        subscribed_event_types=frozenset({"order.created"}),
    )
    fetched = await repo.get(created.id)

    assert fetched is not None
    assert fetched.url == "https://example.com/hook"
    assert fetched.status is EndpointStatus.ACTIVE


async def test_update_changes_only_provided_fields(db_session: AsyncSession) -> None:
    tenant_id = await _make_tenant_id(db_session)
    repo = EndpointRepository(db_session)
    created = await repo.create(
        tenant_id=tenant_id,
        url="https://example.com/hook",
        secret="s3cr3t",
        subscribed_event_types=frozenset({"order.created"}),
    )

    updated = await repo.update(created.id, status=EndpointStatus.DISABLED)

    assert updated.status is EndpointStatus.DISABLED
    assert updated.url == "https://example.com/hook"


async def test_delete_removes_endpoint(db_session: AsyncSession) -> None:
    tenant_id = await _make_tenant_id(db_session)
    repo = EndpointRepository(db_session)
    created = await repo.create(
        tenant_id=tenant_id,
        url="https://example.com/hook",
        secret="s3cr3t",
        subscribed_event_types=frozenset(),
    )

    await repo.delete(created.id)

    assert await repo.get(created.id) is None


async def test_delete_missing_endpoint_raises_lookup_error(db_session: AsyncSession) -> None:
    repo = EndpointRepository(db_session)

    with pytest.raises(NotFoundError):
        await repo.delete(uuid.uuid4())


async def test_list_paginates_with_no_skips_or_duplicates(db_session: AsyncSession) -> None:
    tenant_id = await _make_tenant_id(db_session)
    repo = EndpointRepository(db_session)
    created_ids = []
    for i in range(5):
        endpoint = await repo.create(
            tenant_id=tenant_id,
            url=f"https://example.com/hook-{i}",
            secret="s3cr3t",
            subscribed_event_types=frozenset(),
        )
        created_ids.append(endpoint.id)

    seen_ids: list[uuid.UUID] = []
    cursor: str | None = None
    while True:
        page = await repo.list(tenant_id, cursor=cursor, limit=2)
        seen_ids.extend(e.id for e in page.items)
        if not page.has_more:
            assert page.next_cursor is None
            break
        assert page.next_cursor is not None
        cursor = page.next_cursor

    # Every row in this test shares one Postgres transaction, so `now()` (transaction
    # start time) is identical for all five inserts — the real invariant under test is
    # "no id skipped or duplicated across pages," not creation order.
    assert len(seen_ids) == len(set(seen_ids)) == len(created_ids)
    assert set(seen_ids) == set(created_ids)


async def test_create_defaults_consecutive_failures_to_zero(db_session: AsyncSession) -> None:
    tenant_id = await _make_tenant_id(db_session)
    repo = EndpointRepository(db_session)

    created = await repo.create(
        tenant_id=tenant_id,
        url="https://example.com/hook",
        secret="s3cr3t",
        subscribed_event_types=frozenset(),
    )

    assert created.consecutive_failures == 0
    assert created.breaker_state is BreakerState.CLOSED
    assert created.opened_at is None


async def test_record_delivery_outcome_persists_breaker_transition(
    db_session: AsyncSession,
) -> None:
    tenant_id = await _make_tenant_id(db_session)
    repo = EndpointRepository(db_session)
    created = await repo.create(
        tenant_id=tenant_id,
        url="https://example.com/hook",
        secret="s3cr3t",
        subscribed_event_types=frozenset(),
    )
    opened_at = datetime(2026, 1, 1, tzinfo=UTC)

    updated = await repo.record_delivery_outcome(
        created.id,
        breaker_state=BreakerState.OPEN,
        consecutive_failures=5,
        opened_at=opened_at,
    )

    assert updated.breaker_state is BreakerState.OPEN
    assert updated.consecutive_failures == 5
    assert updated.opened_at == opened_at

    fetched = await repo.get(created.id)
    assert fetched is not None
    assert fetched.breaker_state is BreakerState.OPEN
    assert fetched.consecutive_failures == 5


async def test_set_breaker_state_leaves_consecutive_failures_untouched(
    db_session: AsyncSession,
) -> None:
    tenant_id = await _make_tenant_id(db_session)
    repo = EndpointRepository(db_session)
    created = await repo.create(
        tenant_id=tenant_id,
        url="https://example.com/hook",
        secret="s3cr3t",
        subscribed_event_types=frozenset(),
    )
    opened_at = datetime(2026, 1, 1, tzinfo=UTC)
    await repo.record_delivery_outcome(
        created.id, breaker_state=BreakerState.OPEN, consecutive_failures=5, opened_at=opened_at
    )

    updated = await repo.set_breaker_state(
        created.id, breaker_state=BreakerState.HALF_OPEN, opened_at=opened_at
    )

    assert updated.breaker_state is BreakerState.HALF_OPEN
    assert updated.consecutive_failures == 5  # untouched


async def test_breaker_transitions_update_the_prometheus_gauge(
    db_session: AsyncSession,
) -> None:
    """Backlog item 15 (Phase 5): the circuit_breaker_state Enum gauge
    (relay.infra.metrics) must track the endpoint's real breaker state through the full
    closed -> open -> half_open -> closed cycle, not just the terminal value -- driven
    directly through the repository (rather than the dispatcher end to end, as
    tests/integration/test_circuit_breaker.py does for the domain/DB state) specifically
    because it's the only way to observe the transient half_open value: within one real
    delivery attempt, half_open synchronously flips to closed/open again before control
    returns anywhere a test could inspect it.
    """
    tenant_id = await _make_tenant_id(db_session)
    repo = EndpointRepository(db_session)
    created = await repo.create(
        tenant_id=tenant_id,
        url="https://example.com/hook",
        secret="s3cr3t",
        subscribed_event_types=frozenset(),
    )
    opened_at = datetime(2026, 1, 1, tzinfo=UTC)

    assert _gauge_state(created.id) is None  # never set yet

    await repo.record_delivery_outcome(
        created.id, breaker_state=BreakerState.CLOSED, consecutive_failures=1, opened_at=None
    )
    assert _gauge_state(created.id) == "closed"

    await repo.record_delivery_outcome(
        created.id, breaker_state=BreakerState.OPEN, consecutive_failures=5, opened_at=opened_at
    )
    assert _gauge_state(created.id) == "open"

    await repo.set_breaker_state(
        created.id, breaker_state=BreakerState.HALF_OPEN, opened_at=opened_at
    )
    assert _gauge_state(created.id) == "half_open"

    await repo.record_delivery_outcome(
        created.id, breaker_state=BreakerState.CLOSED, consecutive_failures=0, opened_at=None
    )
    assert _gauge_state(created.id) == "closed"


async def test_list_scopes_to_tenant(db_session: AsyncSession) -> None:
    repo = EndpointRepository(db_session)
    tenant_a = await _make_tenant_id(db_session)
    tenant_b = await _make_tenant_id(db_session)
    await repo.create(
        tenant_id=tenant_a,
        url="https://a.example.com",
        secret="s",
        subscribed_event_types=frozenset(),
    )
    await repo.create(
        tenant_id=tenant_b,
        url="https://b.example.com",
        secret="s",
        subscribed_event_types=frozenset(),
    )

    page = await repo.list(tenant_a)

    assert [e.url for e in page.items] == ["https://a.example.com"]
