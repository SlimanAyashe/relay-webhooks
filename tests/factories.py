"""polyfactory factories for domain entities, used by integration tests that need
realistic data without hand-rolling every field. Fields that represent the common
"freshly created, nothing gone wrong yet" case (revoked_at, deleted_at, opened_at,
breaker_state) default to that rather than polyfactory's random value, since a randomly
revoked/deleted/tripped entity would be a surprising default -- callers wanting that case
override it explicitly, e.g. ApiKeyFactory.build(revoked_at=datetime.now(UTC)).
"""

from datetime import UTC, datetime

from polyfactory.factories import DataclassFactory

from relay.domain.api_keys import ApiKey
from relay.domain.endpoints import BreakerState, Endpoint, EndpointStatus
from relay.domain.events import Event
from relay.domain.tenants import Tenant


class TenantFactory(DataclassFactory[Tenant]):
    __model__ = Tenant

    @classmethod
    def name(cls) -> str:
        return cls.__faker__.company()

    @classmethod
    def created_at(cls) -> datetime:
        return datetime.now(UTC)

    @classmethod
    def deleted_at(cls) -> None:
        return None


class ApiKeyFactory(DataclassFactory[ApiKey]):
    __model__ = ApiKey

    @classmethod
    def key_hash(cls) -> str:
        return f"{cls.__faker__.hexify('^' * 32)}${cls.__faker__.hexify('^' * 64)}"

    @classmethod
    def key_prefix(cls) -> str:
        return cls.__faker__.lexify("????????")

    @classmethod
    def scopes(cls) -> frozenset[str]:
        return frozenset(
            cls.__faker__.random_elements(
                elements=("tenants:read", "endpoints:read", "endpoints:write", "events:write"),
                unique=True,
            )
        )

    @classmethod
    def created_at(cls) -> datetime:
        return datetime.now(UTC)

    @classmethod
    def revoked_at(cls) -> None:
        return None


class EndpointFactory(DataclassFactory[Endpoint]):
    __model__ = Endpoint

    @classmethod
    def url(cls) -> str:
        return f"https://{cls.__faker__.domain_name()}/webhooks"

    @classmethod
    def secret(cls) -> str:
        return cls.__faker__.hexify("^" * 40)

    @classmethod
    def subscribed_event_types(cls) -> frozenset[str]:
        return frozenset(
            cls.__faker__.random_elements(
                elements=("order.created", "order.updated", "order.deleted", "invoice.paid"),
                unique=True,
                length=cls.__faker__.random_int(min=1, max=3),
            )
        )

    @classmethod
    def created_at(cls) -> datetime:
        return datetime.now(UTC)

    @classmethod
    def status(cls) -> EndpointStatus:
        return EndpointStatus.ACTIVE

    @classmethod
    def breaker_state(cls) -> BreakerState:
        return BreakerState.CLOSED

    @classmethod
    def opened_at(cls) -> None:
        return None


class EventFactory(DataclassFactory[Event]):
    __model__ = Event

    @classmethod
    def type(cls) -> str:
        return cls.__faker__.random_element(
            elements=("order.created", "order.updated", "invoice.paid")
        )

    @classmethod
    def payload(cls) -> dict[str, object]:
        return {"id": str(cls.__faker__.uuid4()), "amount": cls.__faker__.random_int(1, 10000)}

    @classmethod
    def idempotency_key(cls) -> str:
        return str(cls.__faker__.uuid4())

    @classmethod
    def created_at(cls) -> datetime:
        return datetime.now(UTC)
