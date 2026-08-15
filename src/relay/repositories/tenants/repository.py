import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from relay.domain.tenants import Tenant
from relay.repositories.tenants.models import TenantModel


def _to_domain(model: TenantModel) -> Tenant:
    return Tenant(
        id=model.id,
        name=model.name,
        created_at=model.created_at,
        deleted_at=model.deleted_at,
    )


class TenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, name: str) -> Tenant:
        model = TenantModel(name=name)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_domain(model)

    async def get(self, tenant_id: uuid.UUID) -> Tenant | None:
        model = await self._session.get(TenantModel, tenant_id)
        return _to_domain(model) if model is not None else None

    async def list(self) -> list[Tenant]:
        result = await self._session.execute(select(TenantModel).order_by(TenantModel.created_at))
        return [_to_domain(model) for model in result.scalars()]
