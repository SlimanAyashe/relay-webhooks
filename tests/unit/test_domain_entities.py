import uuid
from datetime import UTC, datetime

from relay.domain.api_keys import ApiKey
from relay.domain.endpoints import BreakerState, Endpoint, EndpointStatus
from relay.domain.tenants import Tenant

NOW = datetime.now(UTC)


def test_tenant_is_deleted_reflects_deleted_at() -> None:
    active = Tenant(id=uuid.uuid4(), name="acme", created_at=NOW)
    deleted = Tenant(id=uuid.uuid4(), name="acme", created_at=NOW, deleted_at=NOW)

    assert active.is_deleted() is False
    assert deleted.is_deleted() is True


def test_api_key_has_scope_matches_exact_scope() -> None:
    key = ApiKey(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        key_hash="hash",
        key_prefix="pfx",
        scopes=frozenset({"endpoints:read"}),
        created_at=NOW,
    )

    assert key.has_scope("endpoints:read") is True
    assert key.has_scope("endpoints:write") is False


def test_api_key_wildcard_scope_grants_everything() -> None:
    key = ApiKey(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        key_hash="hash",
        key_prefix="pfx",
        scopes=frozenset({"*"}),
        created_at=NOW,
    )

    assert key.has_scope("anything:whatsoever") is True


def test_api_key_is_revoked_reflects_revoked_at() -> None:
    key = ApiKey(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        key_hash="hash",
        key_prefix="pfx",
        scopes=frozenset(),
        created_at=NOW,
        revoked_at=NOW,
    )

    assert key.is_revoked() is True


def test_endpoint_is_subscribed_to_checks_membership() -> None:
    endpoint = Endpoint(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        url="https://example.com/webhook",
        secret="s3cr3t",
        subscribed_event_types=frozenset({"order.created"}),
        created_at=NOW,
    )

    assert endpoint.is_subscribed_to("order.created") is True
    assert endpoint.is_subscribed_to("order.deleted") is False


def test_endpoint_is_active_reflects_status() -> None:
    active = Endpoint(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        url="https://example.com/webhook",
        secret="s3cr3t",
        subscribed_event_types=frozenset(),
        created_at=NOW,
        status=EndpointStatus.ACTIVE,
    )
    disabled = Endpoint(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        url="https://example.com/webhook",
        secret="s3cr3t",
        subscribed_event_types=frozenset(),
        created_at=NOW,
        status=EndpointStatus.DISABLED,
    )

    assert active.is_active() is True
    assert disabled.is_active() is False
    assert disabled.breaker_state is BreakerState.CLOSED
