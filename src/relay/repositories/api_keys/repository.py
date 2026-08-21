import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from relay.domain.api_keys import ApiKey
from relay.domain.errors import NotFoundError
from relay.repositories.api_keys.models import ApiKeyModel


def _to_domain(model: ApiKeyModel) -> ApiKey:
    return ApiKey(
        id=model.id,
        tenant_id=model.tenant_id,
        key_hash=model.key_hash,
        key_prefix=model.key_prefix,
        scopes=frozenset(model.scopes),
        created_at=model.created_at,
        revoked_at=model.revoked_at,
        expires_at=model.expires_at,
    )


class ApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        tenant_id: uuid.UUID,
        key_hash: str,
        key_prefix: str,
        scopes: frozenset[str],
        *,
        expires_at: datetime | None = None,
    ) -> ApiKey:
        model = ApiKeyModel(
            tenant_id=tenant_id,
            key_hash=key_hash,
            key_prefix=key_prefix,
            scopes=list(scopes),
            expires_at=expires_at,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_domain(model)

    async def get(self, key_id: uuid.UUID) -> ApiKey | None:
        model = await self._session.get(ApiKeyModel, key_id)
        return _to_domain(model) if model is not None else None

    async def get_by_prefix(self, key_prefix: str) -> list[ApiKey]:
        """Returns every key matching this prefix (ordinarily zero or one — key_prefix is
        indexed but not DB-unique, so the caller verifies the full key hash against each
        candidate rather than assuming a single match).
        """
        result = await self._session.execute(
            select(ApiKeyModel).where(ApiKeyModel.key_prefix == key_prefix)
        )
        return [_to_domain(model) for model in result.scalars()]

    async def list(self, tenant_id: uuid.UUID) -> list[ApiKey]:
        result = await self._session.execute(
            select(ApiKeyModel)
            .where(ApiKeyModel.tenant_id == tenant_id)
            .order_by(ApiKeyModel.created_at)
        )
        return [_to_domain(model) for model in result.scalars()]

    async def revoke(self, key_id: uuid.UUID) -> ApiKey:
        model = await self._session.get(ApiKeyModel, key_id)
        if model is None:
            raise NotFoundError(f"api key not found: {key_id}")
        model.revoked_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_domain(model)
