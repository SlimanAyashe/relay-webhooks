from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from relay.infra.db import get_sessionmaker
from relay.repositories.api_keys.repository import ApiKeyRepository
from relay.repositories.deliveries.repository import DeliveryRepository
from relay.repositories.delivery_attempts.repository import DeliveryAttemptRepository
from relay.repositories.endpoints.repository import EndpointRepository
from relay.repositories.events.repository import EventRepository
from relay.repositories.outbox.repository import OutboxRepository
from relay.repositories.tenants.repository import TenantRepository


class UnitOfWork:
    """One commit/rollback boundary per service use case. Opens a session and its
    repositories on entry; exit always rolls back unless commit() was called first, so a
    forgotten commit fails safe rather than silently persisting a half-finished use case.
    """

    tenants: TenantRepository
    api_keys: ApiKeyRepository
    endpoints: EndpointRepository
    events: EventRepository
    outbox: OutboxRepository
    deliveries: DeliveryRepository
    delivery_attempts: DeliveryAttemptRepository

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def __aenter__(self) -> "UnitOfWork":
        self._session = self._sessionmaker()
        self.tenants = TenantRepository(self._session)
        self.api_keys = ApiKeyRepository(self._session)
        self.endpoints = EndpointRepository(self._session)
        self.events = EventRepository(self._session)
        self.outbox = OutboxRepository(self._session)
        self.deliveries = DeliveryRepository(self._session)
        self.delivery_attempts = DeliveryAttemptRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.rollback()
        await self._session.close()

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()


def get_unit_of_work() -> UnitOfWork:
    return UnitOfWork(get_sessionmaker())
