import json

from starlette.requests import Request

from relay.api.errors import domain_error_handler
from relay.domain.errors import DomainError, RateLimitExceeded, SsrfBlocked


def _request() -> Request:
    return Request(scope={"type": "http", "path": "/test", "headers": [], "method": "GET"})


async def test_domain_error_handler_maps_unmapped_subclass_to_500() -> None:
    class _WeirdDomainError(DomainError):
        pass

    response = await domain_error_handler(_request(), _WeirdDomainError("surprise"))

    assert response.status_code == 500
    body = json.loads(response.body)
    assert body["status"] == 500
    assert body["trace_id"]


async def test_domain_error_handler_generates_trace_id_when_state_missing() -> None:
    response = await domain_error_handler(_request(), DomainError("bare base class"))

    body = json.loads(response.body)
    assert body["trace_id"]


async def test_rate_limit_exceeded_maps_to_429_with_retry_after_header() -> None:
    response = await domain_error_handler(_request(), RateLimitExceeded(2.3))

    assert response.status_code == 429
    assert response.headers["retry-after"] == "3"  # rounded up, never down
    body = json.loads(response.body)
    assert body["status"] == 429
    assert body["type"] == "/problems/rate-limited"


async def test_rate_limit_exceeded_retry_after_is_at_least_one_second() -> None:
    response = await domain_error_handler(_request(), RateLimitExceeded(0.01))

    assert response.headers["retry-after"] == "1"


async def test_ssrf_blocked_maps_to_422() -> None:
    response = await domain_error_handler(_request(), SsrfBlocked("blocked destination"))

    assert response.status_code == 422
