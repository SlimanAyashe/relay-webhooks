import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from relay.domain.delivery_attempts import AttemptErrorClass
from relay.repositories.deliveries.repository import DeliveryRepository
from relay.repositories.delivery_attempts.repository import DeliveryAttemptRepository
from relay.repositories.endpoints.repository import EndpointRepository
from relay.repositories.events.repository import EventRepository
from relay.repositories.tenants.repository import TenantRepository


async def _make_delivery_id(session: AsyncSession) -> uuid.UUID:
    tenant = await TenantRepository(session).create(name="acme")
    event = await EventRepository(session).create(
        tenant_id=tenant.id, event_type="t", payload={}, idempotency_key=str(uuid.uuid4())
    )
    endpoint = await EndpointRepository(session).create(
        tenant_id=tenant.id,
        url="https://example.com/webhook",
        secret="s3cr3t",
        subscribed_event_types=frozenset({"t"}),
    )
    delivery = await DeliveryRepository(session).create(event_id=event.id, endpoint_id=endpoint.id)
    return delivery.id


async def test_create_records_a_successful_attempt(db_session: AsyncSession) -> None:
    delivery_id = await _make_delivery_id(db_session)

    attempt = await DeliveryAttemptRepository(db_session).create(
        delivery_id=delivery_id,
        attempt_no=1,
        latency_ms=42,
        response_status=200,
        response_snippet="ok",
    )

    assert attempt.response_status == 200
    assert attempt.error_class is None
    assert attempt.response_snippet == "ok"


async def test_create_records_a_timeout_attempt_with_no_status(db_session: AsyncSession) -> None:
    delivery_id = await _make_delivery_id(db_session)

    attempt = await DeliveryAttemptRepository(db_session).create(
        delivery_id=delivery_id,
        attempt_no=1,
        latency_ms=10_000,
        error_class=AttemptErrorClass.TIMEOUT,
    )

    assert attempt.response_status is None
    assert attempt.error_class is AttemptErrorClass.TIMEOUT
