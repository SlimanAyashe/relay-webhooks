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
