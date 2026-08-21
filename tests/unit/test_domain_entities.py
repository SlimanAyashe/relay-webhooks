import uuid
from datetime import UTC, datetime, timedelta

from relay.domain.api_keys import ApiKey
from relay.domain.endpoints import BreakerState, Endpoint, EndpointStatus
from relay.domain.tenants import Tenant

NOW = datetime.now(UTC)


def test_tenant_is_deleted_reflects_deleted_at() -> None:
    active = Tenant(id=uuid.uuid4(), name="acme", created_at=NOW)
    deleted = Tenant(id=uuid.uuid4(), name="acme", created_at=NOW, deleted_at=NOW)

    assert active.is_deleted() is False
    assert deleted.is_deleted() is True


def test_tenant_is_sandbox_defaults_false() -> None:
    tenant = Tenant(id=uuid.uuid4(), name="acme", created_at=NOW)
    sandbox = Tenant(id=uuid.uuid4(), name="sandbox-1", created_at=NOW, is_sandbox=True)

    assert tenant.is_sandbox is False
    assert sandbox.is_sandbox is True


def test_api_key_is_expired_with_no_expiry_is_never_expired() -> None:
    key = ApiKey(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        key_hash="hash",
        key_prefix="pfx",
        scopes=frozenset(),
        created_at=NOW,
    )

    assert key.is_expired(NOW + timedelta(days=3650)) is False


def test_api_key_is_expired_reflects_expires_at_boundary() -> None:
    expires_at = NOW + timedelta(minutes=60)
    key = ApiKey(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        key_hash="hash",
        key_prefix="pfx",
        scopes=frozenset(),
        created_at=NOW,
        expires_at=expires_at,
    )

    assert key.is_expired(expires_at - timedelta(seconds=1)) is False
    assert key.is_expired(expires_at) is True
    assert key.is_expired(expires_at + timedelta(seconds=1)) is True


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
