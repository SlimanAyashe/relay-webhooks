import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from relay.services.deliveries.metrics_service import DeliveryMetrics
from relay.services.sandbox.service import SandboxProvisionResult, SandboxQuotas


class SandboxQuotasRead(BaseModel):
    max_endpoints: int
    max_events: int
    rate_limit_requests_per_second: float
    ttl_minutes: int

    @classmethod
    def from_domain(cls, quotas: SandboxQuotas) -> "SandboxQuotasRead":
        return cls(
            max_endpoints=quotas.max_endpoints,
            max_events=quotas.max_events,
            rate_limit_requests_per_second=quotas.rate_limit_requests_per_second,
            ttl_minutes=quotas.ttl_minutes,
        )


class SandboxCreated(BaseModel):
    """Returned exactly once, at provisioning -- the only time the plaintext API key is
    ever available, same rule as EndpointCreated.secret.
    """

    tenant_id: uuid.UUID
    api_key: str
    expires_at: datetime
    quotas: SandboxQuotasRead

    @classmethod
    def from_result(cls, result: SandboxProvisionResult) -> "SandboxCreated":
        return cls(
            tenant_id=result.tenant.id,
            api_key=result.plaintext_key,
            expires_at=result.expires_at,
            quotas=SandboxQuotasRead.from_domain(result.quotas),
        )


class SignatureVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret: str
    timestamp: int
    body: str = Field(description="The raw request body exactly as sent, as a string.")
    signature: str
    tolerance_seconds: int | None = Field(
        default=None, description="Defaults to Settings.signature_tolerance_seconds."
    )


class SignatureVerifyResponse(BaseModel):
    valid: bool


class MetricsRead(BaseModel):
    queue_depth: int
    in_flight: int
    p95_latency_ms: int | None
    success_rate: float | None
    sample_size: int

    @classmethod
    def from_domain(cls, metrics: DeliveryMetrics) -> "MetricsRead":
        return cls(
            queue_depth=metrics.queue_depth,
            in_flight=metrics.in_flight,
            p95_latency_ms=metrics.p95_latency_ms,
            success_rate=metrics.success_rate,
            sample_size=metrics.sample_size,
        )
