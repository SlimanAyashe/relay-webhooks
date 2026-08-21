import uuid
from datetime import UTC, datetime, timedelta

import pytest
from freezegun import freeze_time
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from relay.domain.errors import SandboxQuotaExceeded
from relay.infra.settings import get_settings
from relay.repositories.unit_of_work import UnitOfWork
from relay.services.sandbox.service import SANDBOX_SCOPES, SandboxService


def _uow(db_engine: AsyncEngine) -> UnitOfWork:
    return UnitOfWork(async_sessionmaker(db_engine, expire_on_commit=False))


async def _make_normal_tenant(db_engine: AsyncEngine) -> uuid.UUID:
    uow = _uow(db_engine)
    async with uow:
        tenant = await uow.tenants.create(name=f"acme-{uuid.uuid4()}")
        await uow.commit()
    return tenant.id


async def test_provision_creates_sandbox_tenant_and_ttl_limited_key(
    db_engine: AsyncEngine,
) -> None:
    settings = get_settings()
    with freeze_time("2026-01-01T00:00:00Z"):
        result = await SandboxService(_uow(db_engine)).provision()

        assert result.tenant.is_sandbox is True
        assert result.api_key.scopes == SANDBOX_SCOPES
        assert result.api_key.expires_at == datetime.now(UTC) + timedelta(
            minutes=settings.sandbox_ttl_minutes
        )
        assert result.quotas.max_endpoints == settings.sandbox_max_endpoints
        assert result.quotas.max_events == settings.sandbox_max_events
        # The plaintext key round-trips through the real hashing/prefix scheme -- not a
        # fixture double -- so it authenticates exactly like any other issued key.
        assert "." in result.plaintext_key


async def test_endpoint_quota_is_a_noop_for_a_normal_tenant(db_engine: AsyncEngine) -> None:
    uow = _uow(db_engine)
    tenant_id = await _make_normal_tenant(db_engine)
    async with uow:
        tenant = await uow.tenants.get(tenant_id)
    assert tenant is not None

    # A normal tenant has no endpoints registered and no cap -- must not raise.
    await SandboxService(uow).assert_endpoint_quota(tenant)


async def test_endpoint_quota_rejects_the_endpoint_at_the_cap(db_engine: AsyncEngine) -> None:
    """Testing scenario (Phase 4, backlog p4-26): a sandbox tenant is rejected once it
    already has Settings.sandbox_max_endpoints endpoints -- the *next* registration, not
    an off-by-one at max-1.
    """
    settings = get_settings()
    uow = _uow(db_engine)
    async with uow:
        tenant = await uow.tenants.create(name=f"sandbox-{uuid.uuid4()}", is_sandbox=True)
        for i in range(settings.sandbox_max_endpoints):
            await uow.endpoints.create(
                tenant_id=tenant.id,
                url=f"https://example.com/webhook-{i}",
                secret="s3cr3t",
                subscribed_event_types=frozenset({"x"}),
            )
        await uow.commit()

    with pytest.raises(SandboxQuotaExceeded, match="endpoints"):
        await SandboxService(uow).assert_endpoint_quota(tenant)


async def test_endpoint_quota_allows_one_below_the_cap(db_engine: AsyncEngine) -> None:
    settings = get_settings()
    uow = _uow(db_engine)
    async with uow:
        tenant = await uow.tenants.create(name=f"sandbox-{uuid.uuid4()}", is_sandbox=True)
        for i in range(settings.sandbox_max_endpoints - 1):
            await uow.endpoints.create(
                tenant_id=tenant.id,
                url=f"https://example.com/webhook-{i}",
                secret="s3cr3t",
                subscribed_event_types=frozenset({"x"}),
            )
        await uow.commit()

    await SandboxService(uow).assert_endpoint_quota(tenant)


async def test_event_quota_rejects_the_event_at_the_cap(db_engine: AsyncEngine) -> None:
    settings = get_settings()
    uow = _uow(db_engine)
    async with uow:
        tenant = await uow.tenants.create(name=f"sandbox-{uuid.uuid4()}", is_sandbox=True)
        for i in range(settings.sandbox_max_events):
            await uow.events.create(
                tenant_id=tenant.id,
                event_type="demo.triggered",
                payload={"i": i},
                idempotency_key=f"idem-{i}",
            )
        await uow.commit()

    with pytest.raises(SandboxQuotaExceeded, match="events"):
        await SandboxService(uow).assert_event_quota(tenant)
