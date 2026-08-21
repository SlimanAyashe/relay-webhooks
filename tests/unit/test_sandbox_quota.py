import pytest

from relay.domain.errors import SandboxQuotaExceeded
from relay.domain.sandbox import check_quota


def test_check_quota_allows_below_the_limit() -> None:
    check_quota(2, 3, resource="endpoints")  # must not raise


def test_check_quota_rejects_at_the_limit() -> None:
    """>= , not >: called before the row that would push the count over the limit is
    created, so a current_count already equal to the limit must reject the next one.
    """
    with pytest.raises(SandboxQuotaExceeded) as exc_info:
        check_quota(3, 3, resource="endpoints")

    assert exc_info.value.resource == "endpoints"
    assert exc_info.value.limit == 3


def test_check_quota_rejects_above_the_limit() -> None:
    with pytest.raises(SandboxQuotaExceeded):
        check_quota(4, 3, resource="events")
