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
