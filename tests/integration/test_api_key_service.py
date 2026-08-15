import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from relay.domain.api_keys.hashing import split_api_key, verify_secret
from relay.domain.errors import NotFoundError
from relay.repositories.unit_of_work import UnitOfWork
from relay.services.api_keys.service import ApiKeyService


async def _make_tenant_id(uow: UnitOfWork) -> uuid.UUID:
    async with uow:
        tenant = await uow.tenants.create(name=f"acme-{uuid.uuid4()}")
        await uow.commit()
    return tenant.id


def _service(db_engine: AsyncEngine) -> tuple[ApiKeyService, UnitOfWork]:
    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)
    uow = UnitOfWork(sessionmaker)
    return ApiKeyService(uow), uow


async def test_issue_returns_a_verifiable_plaintext_key_exactly_once(
    db_engine: AsyncEngine,
) -> None:
    service, uow = _service(db_engine)
    tenant_id = await _make_tenant_id(uow)

    api_key, plaintext_key = await service.issue(tenant_id, frozenset({"endpoints:read"}))

    split = split_api_key(plaintext_key)
    assert split is not None
    prefix, secret = split
    assert prefix == api_key.key_prefix
    assert verify_secret(secret, api_key.key_hash) is True
    assert api_key.scopes == frozenset({"endpoints:read"})
    assert api_key.is_revoked() is False


async def test_revoke_marks_key_revoked(db_engine: AsyncEngine) -> None:
    service, uow = _service(db_engine)
    tenant_id = await _make_tenant_id(uow)
    api_key, _ = await service.issue(tenant_id, frozenset({"*"}))

    revoked = await service.revoke(api_key.id, tenant_id)

    assert revoked.is_revoked() is True
    async with uow:
        persisted = await uow.api_keys.get(api_key.id)
    assert persisted is not None
    assert persisted.is_revoked() is True


async def test_revoke_wrong_tenant_raises_lookup_error(db_engine: AsyncEngine) -> None:
    service, uow = _service(db_engine)
    tenant_id = await _make_tenant_id(uow)
    other_tenant_id = await _make_tenant_id(uow)
    api_key, _ = await service.issue(tenant_id, frozenset({"*"}))

    with pytest.raises(NotFoundError):
        await service.revoke(api_key.id, other_tenant_id)

    async with uow:
        persisted = await uow.api_keys.get(api_key.id)
    assert persisted is not None
    assert persisted.is_revoked() is False


async def test_rotate_revokes_old_and_issues_new_with_same_scopes(
    db_engine: AsyncEngine,
) -> None:
    service, uow = _service(db_engine)
    tenant_id = await _make_tenant_id(uow)
    old_key, _ = await service.issue(tenant_id, frozenset({"endpoints:write"}))

    new_key, new_plaintext = await service.rotate(old_key.id, tenant_id)

    assert new_key.id != old_key.id
    assert new_key.scopes == old_key.scopes

    async with uow:
        persisted_old = await uow.api_keys.get(old_key.id)
        persisted_new = await uow.api_keys.get(new_key.id)
    assert persisted_old is not None
    assert persisted_old.is_revoked() is True
    assert persisted_new is not None
    assert persisted_new.is_revoked() is False

    split = split_api_key(new_plaintext)
    assert split is not None
    _, secret = split
    assert verify_secret(secret, new_key.key_hash) is True


async def test_rotate_wrong_tenant_raises_lookup_error_and_changes_nothing(
    db_engine: AsyncEngine,
) -> None:
    service, uow = _service(db_engine)
    tenant_id = await _make_tenant_id(uow)
    other_tenant_id = await _make_tenant_id(uow)
    api_key, _ = await service.issue(tenant_id, frozenset({"*"}))

    with pytest.raises(NotFoundError):
        await service.rotate(api_key.id, other_tenant_id)

    async with uow:
        persisted = await uow.api_keys.get(api_key.id)
        tenant_keys = await uow.api_keys.list(tenant_id)
    assert persisted is not None
    assert persisted.is_revoked() is False
    assert len(tenant_keys) == 1
