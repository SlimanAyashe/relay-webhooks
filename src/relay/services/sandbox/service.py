import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from relay.domain.api_keys import ApiKey
from relay.domain.sandbox import check_quota
from relay.domain.tenants import Tenant
from relay.infra.settings import Settings, get_settings
from relay.repositories.unit_of_work import UnitOfWork
from relay.services.api_keys.service import ApiKeyService

# A sandbox key gets every scope an interviewer's walkthrough of the console actually
# exercises -- registering endpoints, triggering events, inspecting the DLQ, replaying a
# dead delivery -- reusing the same scope strings the normal /v1 routers already require,
# not a parallel "sandbox" scope namespace.
SANDBOX_SCOPES = frozenset(
    {"endpoints:read", "endpoints:write", "events:write", "dlq:read", "deliveries:replay"}
)


@dataclass(frozen=True, slots=True)
class SandboxQuotas:
    max_endpoints: int
    max_events: int
    rate_limit_requests_per_second: float
    ttl_minutes: int


@dataclass(frozen=True, slots=True)
class SandboxProvisionResult:
    tenant: Tenant
    api_key: ApiKey
    plaintext_key: str
    expires_at: datetime
    quotas: SandboxQuotas


class SandboxService:
    """Provisions self-serve, TTL-limited sandbox tenants for the demo console (POST
    /v1/sandbox) and enforces their hard-capped quotas -- independent of, and far
    tighter than, the per-tenant rate limiter that applies to every tenant regardless
    (relay.infra.rate_limit). See docs/adr/0006-phase-4-demo-console.md.
    """

    def __init__(self, uow: UnitOfWork, *, settings: Settings | None = None) -> None:
        self._uow = uow
        self._settings = settings or get_settings()

    async def provision(self) -> SandboxProvisionResult:
        settings = self._settings
        expires_at = datetime.now(UTC) + timedelta(minutes=settings.sandbox_ttl_minutes)

        async with self._uow:
            tenant = await self._uow.tenants.create(
                name=f"sandbox-{uuid.uuid4().hex[:8]}", is_sandbox=True
            )
            await self._uow.commit()

        api_key, plaintext_key = await ApiKeyService(self._uow).issue(
            tenant.id, SANDBOX_SCOPES, expires_at=expires_at
        )

        return SandboxProvisionResult(
            tenant=tenant,
            api_key=api_key,
            plaintext_key=plaintext_key,
            expires_at=expires_at,
            quotas=SandboxQuotas(
                max_endpoints=settings.sandbox_max_endpoints,
                max_events=settings.sandbox_max_events,
                rate_limit_requests_per_second=settings.sandbox_rate_limit_requests_per_second,
                ttl_minutes=settings.sandbox_ttl_minutes,
            ),
        )

    async def assert_endpoint_quota(self, tenant: Tenant) -> None:
        """No-op for a non-sandbox tenant -- only sandbox tenants carry this cap."""
        if not tenant.is_sandbox:
            return
        async with self._uow:
            count = await self._uow.endpoints.count_for_tenant(tenant.id)
        check_quota(count, self._settings.sandbox_max_endpoints, resource="endpoints")

    async def assert_event_quota(self, tenant: Tenant) -> None:
        if not tenant.is_sandbox:
            return
        async with self._uow:
            count = await self._uow.events.count_for_tenant(tenant.id)
        check_quota(count, self._settings.sandbox_max_events, resource="events")
