import secrets
import uuid
from urllib.parse import urlparse

from relay.domain.endpoints import Endpoint, EndpointStatus
from relay.domain.errors import NotFoundError, ValidationError
from relay.repositories.pagination import Page
from relay.repositories.unit_of_work import UnitOfWork

_SECRET_ENTROPY_BYTES = 32


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValidationError(f"endpoint url must be https: {url!r}")
    if not parsed.netloc:
        raise ValidationError(f"endpoint url must have a host: {url!r}")


def _validate_event_types(event_types: frozenset[str]) -> None:
    if not event_types:
        raise ValidationError("subscribed_event_types must not be empty")
    if any(not event_type.strip() for event_type in event_types):
        raise ValidationError("subscribed_event_types must not contain blank entries")


class EndpointService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def register(
        self, tenant_id: uuid.UUID, url: str, subscribed_event_types: frozenset[str]
    ) -> Endpoint:
        _validate_url(url)
        _validate_event_types(subscribed_event_types)
        secret = secrets.token_urlsafe(_SECRET_ENTROPY_BYTES)
        async with self._uow:
            endpoint = await self._uow.endpoints.create(
                tenant_id=tenant_id,
                url=url,
                secret=secret,
                subscribed_event_types=subscribed_event_types,
            )
            await self._uow.commit()
        return endpoint

    async def get(self, endpoint_id: uuid.UUID, tenant_id: uuid.UUID) -> Endpoint:
        async with self._uow:
            endpoint = await self._uow.endpoints.get(endpoint_id)
        if endpoint is None or endpoint.tenant_id != tenant_id:
            raise NotFoundError(f"endpoint not found for tenant {tenant_id!r}: {endpoint_id!r}")
        return endpoint

    async def list(
        self, tenant_id: uuid.UUID, *, cursor: str | None = None, limit: int = 50
    ) -> Page[Endpoint]:
        async with self._uow:
            return await self._uow.endpoints.list(tenant_id, cursor=cursor, limit=limit)

    async def update(
        self,
        endpoint_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        url: str | None = None,
        subscribed_event_types: frozenset[str] | None = None,
        status: EndpointStatus | None = None,
    ) -> Endpoint:
        if url is not None:
            _validate_url(url)
        if subscribed_event_types is not None:
            _validate_event_types(subscribed_event_types)

        async with self._uow:
            existing = await self._uow.endpoints.get(endpoint_id)
            if existing is None or existing.tenant_id != tenant_id:
                raise NotFoundError(f"endpoint not found for tenant {tenant_id!r}: {endpoint_id!r}")

            updated = await self._uow.endpoints.update(
                endpoint_id,
                url=url,
                subscribed_event_types=subscribed_event_types,
                status=status,
            )
            await self._uow.commit()
        return updated

    async def delete(self, endpoint_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        async with self._uow:
            existing = await self._uow.endpoints.get(endpoint_id)
            if existing is None or existing.tenant_id != tenant_id:
                raise NotFoundError(f"endpoint not found for tenant {tenant_id!r}: {endpoint_id!r}")

            await self._uow.endpoints.delete(endpoint_id)
            await self._uow.commit()
