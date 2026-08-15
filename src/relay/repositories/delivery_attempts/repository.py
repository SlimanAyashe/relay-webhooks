import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from relay.domain.delivery_attempts import AttemptErrorClass, DeliveryAttempt
from relay.repositories.delivery_attempts.models import DeliveryAttemptModel


def _to_domain(model: DeliveryAttemptModel) -> DeliveryAttempt:
    return DeliveryAttempt(
        id=model.id,
        delivery_id=model.delivery_id,
        attempt_no=model.attempt_no,
        latency_ms=model.latency_ms,
        created_at=model.created_at,
        response_status=model.response_status,
        error_class=AttemptErrorClass(model.error_class) if model.error_class else None,
        request_snippet=model.request_snippet,
        response_snippet=model.response_snippet,
    )


class DeliveryAttemptRepository:
    """Append-only: attempts are an immutable log, so there is deliberately no update or
    delete operation here.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        delivery_id: uuid.UUID,
        attempt_no: int,
        latency_ms: int,
        *,
        response_status: int | None = None,
        error_class: AttemptErrorClass | None = None,
        request_snippet: str | None = None,
        response_snippet: str | None = None,
    ) -> DeliveryAttempt:
        model = DeliveryAttemptModel(
            delivery_id=delivery_id,
            attempt_no=attempt_no,
            latency_ms=latency_ms,
            response_status=response_status,
            error_class=error_class.value if error_class is not None else None,
            request_snippet=request_snippet,
            response_snippet=response_snippet,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_domain(model)
