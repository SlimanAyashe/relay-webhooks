import json

from starlette.requests import Request

from relay.api.errors import domain_error_handler
from relay.domain.errors import DomainError


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
