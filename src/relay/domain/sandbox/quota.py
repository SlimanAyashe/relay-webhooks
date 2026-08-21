"""Pure sandbox-quota decision logic, zero I/O -- mirrors relay.domain.endpoints.breaker's
shape (a pure function deciding an outcome from numbers the caller already looked up).
Counting rows is the repository's job; deciding whether a count is over the limit is a
domain-level policy, not a query concern.
"""

from relay.domain.errors import SandboxQuotaExceeded


def check_quota(current_count: int, limit: int, *, resource: str) -> None:
    """Raises SandboxQuotaExceeded if `current_count` has already reached `limit`.
    Deliberately `>=`, not `>`: called *before* the row that would push the count over
    the limit is created, so "3 endpoints already exist, limit is 3" must reject the 4th.
    """
    if current_count >= limit:
        raise SandboxQuotaExceeded(resource, limit)
