class DomainError(Exception):
    """Base for typed business-rule violations raised by services and repositories.

    No HTTP concepts here (no status_code, no framework imports) -- relay.api's central
    exception handler owns the mapping from these to RFC 9457 responses, keeping this
    layer's independence contract intact.
    """


class NotFoundError(DomainError):
    """A requested resource does not exist, or the caller isn't allowed to know it does."""


class ConflictError(DomainError):
    """The request conflicts with existing state (e.g. a reused idempotency key)."""


class ValidationError(DomainError):
    """The request violates a business rule beyond what wire-schema validation checks."""


class RateLimitExceeded(DomainError):
    """The caller exceeded its configured per-tenant rate-limit budget. Carries
    `retry_after_seconds` (from the token bucket's own refill math) so the API layer can
    set a `Retry-After` header without recomputing it.
    """

    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"rate limit exceeded, retry after {retry_after_seconds:.2f}s")


class PayloadTooLarge(DomainError):
    """The raw request body exceeded Settings.event_payload_max_bytes -- closes off using
    public event ingest (sandbox or otherwise) as a large-payload relay.
    """

    def __init__(self, actual_bytes: int, max_bytes: int) -> None:
        self.actual_bytes = actual_bytes
        self.max_bytes = max_bytes
        super().__init__(f"payload too large: {actual_bytes} bytes, max {max_bytes}")


class SandboxQuotaExceeded(DomainError):
    """A sandbox tenant (Tenant.is_sandbox) tried to exceed one of its hard-capped
    quotas (max endpoints, max events) -- independent of, and far tighter than, the
    per-tenant rate limiter that applies to every tenant. See relay.domain.sandbox.quota.
    """

    def __init__(self, resource: str, limit: int) -> None:
        self.resource = resource
        self.limit = limit
        super().__init__(f"sandbox quota exceeded: max {limit} {resource}")


class SsrfBlocked(DomainError):
    """A destination URL resolved to a denied network (loopback/RFC1918/link-local/CGNAT/
    IPv6 ULA/cloud metadata) or targeted a non-allow-listed port.

    Not currently raised across the API boundary: outbound delivery attempts classify
    this outcome as `AttemptErrorClass.SSRF_BLOCKED` on the recorded attempt instead of
    raising, since a blocked destination is an expected outcome to log, not a request the
    API rejects synchronously. Kept in the shared error hierarchy (with an HTTP mapping
    below) anyway, since SSRF validation is a domain-level policy rather than an HTTP
    concern, and a future endpoint-registration-time pre-check could raise it directly.
    """
