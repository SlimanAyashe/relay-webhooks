import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from relay.repositories.api_keys.repository import ApiKeyRepository
from relay.repositories.tenants.repository import TenantRepository


async def _make_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    tenant = await TenantRepository(db_session).create(name="acme")
    return tenant.id


async def test_create_and_get_round_trip(db_session: AsyncSession) -> None:
    tenant_id = await _make_tenant_id(db_session)
    repo = ApiKeyRepository(db_session)

    created = await repo.create(
        tenant_id=tenant_id,
        key_hash="hash-value",
        key_prefix="pfx_abc",
        scopes=frozenset({"endpoints:read"}),
    )
    fetched = await repo.get(created.id)

    assert fetched is not None
    assert fetched.key_prefix == "pfx_abc"
    assert fetched.scopes == frozenset({"endpoints:read"})
    assert fetched.is_revoked() is False


async def test_get_by_prefix_finds_matching_key(db_session: AsyncSession) -> None:
    tenant_id = await _make_tenant_id(db_session)
    repo = ApiKeyRepository(db_session)
    created = await repo.create(
        tenant_id=tenant_id, key_hash="h", key_prefix="pfx_unique", scopes=frozenset({"*"})
    )

    matches = await repo.get_by_prefix("pfx_unique")

    assert [m.id for m in matches] == [created.id]


async def test_get_by_prefix_no_match_returns_empty_list(db_session: AsyncSession) -> None:
    repo = ApiKeyRepository(db_session)

    assert await repo.get_by_prefix("pfx_does_not_exist") == []


async def test_list_scopes_to_tenant(db_session: AsyncSession) -> None:
    repo = ApiKeyRepository(db_session)
    tenant_a = await _make_tenant_id(db_session)
    tenant_b = await _make_tenant_id(db_session)
    await repo.create(tenant_id=tenant_a, key_hash="h1", key_prefix="p1", scopes=frozenset({"*"}))
    await repo.create(tenant_id=tenant_b, key_hash="h2", key_prefix="p2", scopes=frozenset({"*"}))

    tenant_a_keys = await repo.list(tenant_a)

    assert [k.key_prefix for k in tenant_a_keys] == ["p1"]


async def test_revoke_sets_revoked_at(db_session: AsyncSession) -> None:
    tenant_id = await _make_tenant_id(db_session)
    repo = ApiKeyRepository(db_session)
    created = await repo.create(
        tenant_id=tenant_id, key_hash="h", key_prefix="p", scopes=frozenset({"*"})
    )
    assert created.is_revoked() is False

    revoked = await repo.revoke(created.id)

    assert revoked.is_revoked() is True


async def test_revoke_missing_key_raises_lookup_error(db_session: AsyncSession) -> None:
    repo = ApiKeyRepository(db_session)

    with pytest.raises(LookupError):
        await repo.revoke(uuid.uuid4())
