from relay.api.v1.endpoints.schemas import EndpointRead
from relay.api.v1.events.schemas import EventRead
from relay.api.v1.tenants.schemas import TenantRead
from relay.domain.endpoints import BreakerState, EndpointStatus
from tests.factories import ApiKeyFactory, EndpointFactory, EventFactory, TenantFactory


def test_tenant_factory_builds_a_usable_entity() -> None:
    tenant = TenantFactory.build()

    assert tenant.is_deleted() is False
    TenantRead.from_domain(tenant)  # doesn't raise


def test_api_key_factory_builds_an_unrevoked_key_with_working_scope_checks() -> None:
    key = ApiKeyFactory.build()

    assert key.is_revoked() is False
    assert key.has_scope(next(iter(key.scopes))) is True
    assert key.has_scope("not-a-granted-scope") is False


def test_endpoint_factory_builds_an_active_closed_breaker_endpoint() -> None:
    endpoint = EndpointFactory.build()

    assert endpoint.status is EndpointStatus.ACTIVE
    assert endpoint.breaker_state is BreakerState.CLOSED
    assert endpoint.url.startswith("https://")
    assert len(endpoint.subscribed_event_types) >= 1
    EndpointRead.from_domain(endpoint)  # doesn't raise


def test_event_factory_builds_a_usable_entity() -> None:
    event = EventFactory.build()

    assert event.payload
    EventRead.from_domain(event)  # doesn't raise


def test_factories_build_distinct_entities_each_call() -> None:
    first = TenantFactory.build()
    second = TenantFactory.build()

    assert first.id != second.id
