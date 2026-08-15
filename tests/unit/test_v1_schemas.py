import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from relay.api.v1.api_keys.schemas import ApiKeyIssued, ApiKeyRead
from relay.api.v1.endpoints.schemas import EndpointCreated, EndpointRead
from relay.api.v1.events.schemas import EventCreate, EventRead
from relay.api.v1.tenants.schemas import TenantCreate, TenantRead
from relay.domain.api_keys import ApiKey
from relay.domain.endpoints import Endpoint
from relay.domain.events import Event
from relay.domain.tenants import Tenant

NOW = datetime.now(UTC)


def test_tenant_create_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TenantCreate.model_validate({"name": "acme", "unexpected": "field"})


def test_tenant_read_from_domain_round_trips_fields() -> None:
    tenant = Tenant(id=uuid.uuid4(), name="acme", created_at=NOW)
    read = TenantRead.from_domain(tenant)
    assert read.id == tenant.id
    assert read.name == "acme"
    assert read.deleted_at is None


def test_api_key_read_never_exposes_hash() -> None:
    key = ApiKey(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        key_hash="super-secret-hash",
        key_prefix="pfx_abc",
        scopes=frozenset({"endpoints:read"}),
        created_at=NOW,
    )
    read = ApiKeyRead.from_domain(key)
    assert "key_hash" not in ApiKeyRead.model_fields
    assert read.key_prefix == "pfx_abc"


def test_api_key_issued_carries_plaintext_key_once() -> None:
    key = ApiKey(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        key_hash="hash",
        key_prefix="pfx_abc",
        scopes=frozenset({"*"}),
        created_at=NOW,
    )
    issued = ApiKeyIssued.issued_from_domain(key, "pfx_abc.plaintext-secret")
    assert issued.key == "pfx_abc.plaintext-secret"
    assert issued.key_prefix == "pfx_abc"


def test_endpoint_read_never_exposes_secret() -> None:
    assert "secret" not in EndpointRead.model_fields
    assert "secret" in EndpointCreated.model_fields


def test_endpoint_created_from_domain_carries_secret() -> None:
    endpoint = Endpoint(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        url="https://example.com/webhook",
        secret="s3cr3t",
        subscribed_event_types=frozenset({"order.created"}),
        created_at=NOW,
    )
    created = EndpointCreated.created_from_domain(endpoint)
    assert created.secret == "s3cr3t"
    assert created.subscribed_event_types == ["order.created"]


def test_event_create_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        EventCreate.model_validate({"type": "order.created", "payload": {}, "extra": 1})


def test_event_read_from_domain_round_trips_payload() -> None:
    event = Event(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        type="order.created",
        payload={"order_id": "123"},
        idempotency_key="idem-1",
        created_at=NOW,
    )
    read = EventRead.from_domain(event)
    assert read.payload == {"order_id": "123"}
    assert read.idempotency_key == "idem-1"
