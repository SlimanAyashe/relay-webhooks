import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from freezegun import freeze_time
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from relay.api.auth import _authenticate, require_scope
from relay.repositories.unit_of_work import UnitOfWork
from relay.services.api_keys.service import ApiKeyService


def _uow(db_engine: AsyncEngine) -> UnitOfWork:
    return UnitOfWork(async_sessionmaker(db_engine, expire_on_commit=False))


async def _make_tenant_and_key(
    db_engine: AsyncEngine,
    scopes: frozenset[str] = frozenset({"endpoints:read"}),
    *,
    expires_at: datetime | None = None,
) -> tuple[uuid.UUID, str]:
    uow = _uow(db_engine)
    async with uow:
        tenant = await uow.tenants.create(name=f"acme-{uuid.uuid4()}")
        await uow.commit()
    service = ApiKeyService(uow)
    _, plaintext_key = await service.issue(tenant.id, scopes, expires_at=expires_at)
    return tenant.id, plaintext_key


async def test_authenticate_rejects_missing_key(db_engine: AsyncEngine) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await _authenticate(None, _uow(db_engine))
    assert exc_info.value.status_code == 401


async def test_authenticate_rejects_malformed_key(db_engine: AsyncEngine) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await _authenticate("no-separator-here", _uow(db_engine))
    assert exc_info.value.status_code == 401


async def test_authenticate_rejects_unknown_prefix(db_engine: AsyncEngine) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await _authenticate("unknown-prefix.unknown-secret", _uow(db_engine))
    assert exc_info.value.status_code == 401


async def test_authenticate_rejects_wrong_secret(db_engine: AsyncEngine) -> None:
    _, plaintext_key = await _make_tenant_and_key(db_engine)
    prefix = plaintext_key.split(".", 1)[0]

    with pytest.raises(HTTPException) as exc_info:
        await _authenticate(f"{prefix}.wrong-secret", _uow(db_engine))
    assert exc_info.value.status_code == 401


async def test_authenticate_rejects_revoked_key(db_engine: AsyncEngine) -> None:
    tenant_id, plaintext_key = await _make_tenant_and_key(db_engine)
    uow = _uow(db_engine)
    async with uow:
        keys = await uow.api_keys.list(tenant_id)
        await uow.api_keys.revoke(keys[0].id)
        await uow.commit()

    with pytest.raises(HTTPException) as exc_info:
        await _authenticate(plaintext_key, _uow(db_engine))
    assert exc_info.value.status_code == 401


async def test_authenticate_rejects_expired_sandbox_key(db_engine: AsyncEngine) -> None:
    """Testing scenario (Phase 4, backlog p4-25): a sandbox key's TTL elapsing rejects it
    exactly like a revoked key, without a real 60-minute wait.
    """
    with freeze_time("2026-01-01T00:00:00Z") as frozen:
        expires_at = datetime.now(UTC) + timedelta(minutes=60)
        _, plaintext_key = await _make_tenant_and_key(db_engine, expires_at=expires_at)

        # Still valid one second before expiry.
        frozen.move_to(expires_at - timedelta(seconds=1))
        context = await _authenticate(plaintext_key, _uow(db_engine))
        assert context.api_key.is_expired(datetime.now(UTC)) is False

        # Expired exactly at (and past) the TTL boundary.
        frozen.move_to(expires_at)
        with pytest.raises(HTTPException) as exc_info:
            await _authenticate(plaintext_key, _uow(db_engine))
        assert exc_info.value.status_code == 401


async def test_authenticate_accepts_key_with_no_expiry(db_engine: AsyncEngine) -> None:
    """A normal (non-sandbox) tenant's key has expires_at=None and never expires."""
    with freeze_time("2026-01-01T00:00:00Z") as frozen:
        _, plaintext_key = await _make_tenant_and_key(db_engine, expires_at=None)
        frozen.move_to("2030-01-01T00:00:00Z")

        context = await _authenticate(plaintext_key, _uow(db_engine))

        assert context.api_key.is_expired(datetime.now(UTC)) is False


async def test_authenticate_accepts_valid_key_and_scopes_to_owning_tenant(
    db_engine: AsyncEngine,
) -> None:
    tenant_id, plaintext_key = await _make_tenant_and_key(db_engine)

    context = await _authenticate(plaintext_key, _uow(db_engine))

    assert context.tenant.id == tenant_id
    assert context.api_key.is_revoked() is False


async def test_require_scope_rejects_key_without_scope(db_engine: AsyncEngine) -> None:
    _, plaintext_key = await _make_tenant_and_key(db_engine, scopes=frozenset({"events:write"}))
    dependency = require_scope("endpoints:write")

    with pytest.raises(HTTPException) as exc_info:
        await dependency(presented_key=plaintext_key, uow=_uow(db_engine))
    assert exc_info.value.status_code == 403


async def test_require_scope_accepts_key_with_matching_scope(db_engine: AsyncEngine) -> None:
    _, plaintext_key = await _make_tenant_and_key(db_engine, scopes=frozenset({"endpoints:write"}))
    dependency = require_scope("endpoints:write")

    context = await dependency(presented_key=plaintext_key, uow=_uow(db_engine))

    assert context.api_key.has_scope("endpoints:write") is True


async def test_require_scope_wildcard_key_passes_any_scope(db_engine: AsyncEngine) -> None:
    _, plaintext_key = await _make_tenant_and_key(db_engine, scopes=frozenset({"*"}))
    dependency = require_scope("anything:at-all")

    context = await dependency(presented_key=plaintext_key, uow=_uow(db_engine))

    assert context.api_key.has_scope("anything:at-all") is True
