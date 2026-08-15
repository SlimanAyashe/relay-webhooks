import uuid

from relay.domain.events import Event
from relay.repositories.events.repository import IdempotencyKeyConflict
from relay.repositories.unit_of_work import UnitOfWork


class DifferingBodyConflict(Exception):
    """The idempotency key was reused with a type/payload that doesn't match the first
    request that used it -- the caller should surface this as 409, not silently overwrite
    or silently replay the wrong event.
    """

    def __init__(self, tenant_id: uuid.UUID, idempotency_key: str) -> None:
        self.tenant_id = tenant_id
        self.idempotency_key = idempotency_key
        super().__init__(
            f"idempotency key reused with a differing body for tenant {tenant_id!r}: "
            f"{idempotency_key!r}"
        )


class EventIngestService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def ingest(
        self,
        tenant_id: uuid.UUID,
        event_type: str,
        payload: dict[str, object],
        idempotency_key: str,
    ) -> Event:
        """Idempotency-Key semantics: same key + identical (type, payload) returns the
        original event unchanged (no second row inserted); same key + a differing body
        raises DifferingBodyConflict.
        """
        async with self._uow:
            existing = await self._uow.events.get_by_idempotency_key(tenant_id, idempotency_key)
            if existing is not None:
                return self._resolve(existing, tenant_id, event_type, payload, idempotency_key)

            try:
                event = await self._uow.events.create(
                    tenant_id=tenant_id,
                    event_type=event_type,
                    payload=payload,
                    idempotency_key=idempotency_key,
                )
            except IdempotencyKeyConflict:
                # Lost a race against a concurrent request using the same key -- the
                # SAVEPOINT rollback inside EventRepository.create() left this
                # transaction otherwise intact, so we can just look up what won.
                winner = await self._uow.events.get_by_idempotency_key(tenant_id, idempotency_key)
                if winner is None:
                    raise RuntimeError(
                        "idempotency conflict but no row found for "
                        f"({tenant_id!r}, {idempotency_key!r})"
                    ) from None
                return self._resolve(winner, tenant_id, event_type, payload, idempotency_key)

            await self._uow.commit()
            return event

    @staticmethod
    def _resolve(
        existing: Event,
        tenant_id: uuid.UUID,
        event_type: str,
        payload: dict[str, object],
        idempotency_key: str,
    ) -> Event:
        if existing.type == event_type and existing.payload == payload:
            return existing
        raise DifferingBodyConflict(tenant_id, idempotency_key)
