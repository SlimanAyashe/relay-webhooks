import uuid

from relay.domain.api_keys import ApiKey
from relay.domain.api_keys.hashing import generate_api_key
from relay.repositories.unit_of_work import UnitOfWork


class ApiKeyService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def issue(self, tenant_id: uuid.UUID, scopes: frozenset[str]) -> tuple[ApiKey, str]:
        """Returns (api_key, plaintext_key). The plaintext key is never persisted or
        retrievable again after this call returns.
        """
        plaintext_key, key_prefix, key_hash = generate_api_key()
        async with self._uow:
            api_key = await self._uow.api_keys.create(
                tenant_id=tenant_id, key_hash=key_hash, key_prefix=key_prefix, scopes=scopes
            )
            await self._uow.commit()
        return api_key, plaintext_key

    async def rotate(self, key_id: uuid.UUID, tenant_id: uuid.UUID) -> tuple[ApiKey, str]:
        """Revokes the existing key and issues a replacement with the same scopes,
        atomically in one unit-of-work — never a window where both are simultaneously
        revoked, or where issuance succeeds but the old key is never revoked.
        """
        async with self._uow:
            existing = await self._uow.api_keys.get(key_id)
            if existing is None or existing.tenant_id != tenant_id:
                raise LookupError(f"api key not found for tenant {tenant_id!r}: {key_id!r}")

            await self._uow.api_keys.revoke(key_id)
            plaintext_key, key_prefix, key_hash = generate_api_key()
            new_key = await self._uow.api_keys.create(
                tenant_id=tenant_id,
                key_hash=key_hash,
                key_prefix=key_prefix,
                scopes=existing.scopes,
            )
            await self._uow.commit()
        return new_key, plaintext_key

    async def revoke(self, key_id: uuid.UUID, tenant_id: uuid.UUID) -> ApiKey:
        async with self._uow:
            existing = await self._uow.api_keys.get(key_id)
            if existing is None or existing.tenant_id != tenant_id:
                raise LookupError(f"api key not found for tenant {tenant_id!r}: {key_id!r}")

            revoked = await self._uow.api_keys.revoke(key_id)
            await self._uow.commit()
        return revoked
