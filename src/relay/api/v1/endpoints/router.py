import uuid

from fastapi import APIRouter, Depends, Query, status

from relay.api.auth import AuthContext, require_scope
from relay.api.openapi import problem_responses
from relay.api.v1.endpoints.schemas import (
    EndpointCreate,
    EndpointCreated,
    EndpointListResponse,
    EndpointRead,
    EndpointUpdate,
)
from relay.infra.settings import Settings, get_settings
from relay.repositories.unit_of_work import UnitOfWork, get_unit_of_work
from relay.services.endpoints.service import EndpointService
from relay.services.sandbox.service import SandboxService

router = APIRouter(prefix="/v1/endpoints", tags=["endpoints"])

_require_read = require_scope("endpoints:read")
_require_write = require_scope("endpoints:write")


async def _quota_checked_write_auth(
    auth: AuthContext = Depends(_require_write),
    uow: UnitOfWork = Depends(get_unit_of_work),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    """Runs after authentication and before endpoint creation, enforcing the Phase 4
    sandbox's hard-capped endpoint count -- a no-op for a normal (non-sandbox) tenant.
    Reuses the same `uow` the handler itself receives (FastAPI caches Depends(get_unit_of_work)
    per request), so this is one extra small read, not a second database connection.
    """
    await SandboxService(uow, settings=settings).assert_endpoint_quota(auth.tenant)
    return auth


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=EndpointCreated,
    responses=problem_responses(401, 403, 422),
    summary="Register a new endpoint",
    description=(
        "Registers a webhook endpoint for the authenticated tenant. The response "
        "includes the HMAC signing secret -- the only time it's ever returned. A "
        "sandbox tenant is capped at a small fixed number of endpoints (403 once "
        "exceeded); a normal tenant has no such cap."
    ),
)
async def create_endpoint(
    body: EndpointCreate,
    auth: AuthContext = Depends(_quota_checked_write_auth),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> EndpointCreated:
    service = EndpointService(uow)
    endpoint = await service.register(
        auth.tenant.id, str(body.url), frozenset(body.subscribed_event_types)
    )
    return EndpointCreated.created_from_domain(endpoint)


@router.get(
    "",
    response_model=EndpointListResponse,
    responses=problem_responses(401, 403, 422),
    summary="List endpoints",
    description="Cursor-paginated: pass the previous response's next_cursor to page forward.",
)
async def list_endpoints(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(_require_read),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> EndpointListResponse:
    service = EndpointService(uow)
    page = await service.list(auth.tenant.id, cursor=cursor, limit=limit)
    return EndpointListResponse(
        items=[EndpointRead.from_domain(endpoint) for endpoint in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.get(
    "/{endpoint_id}",
    response_model=EndpointRead,
    responses=problem_responses(401, 403, 404),
    summary="Get an endpoint",
)
async def get_endpoint(
    endpoint_id: uuid.UUID,
    auth: AuthContext = Depends(_require_read),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> EndpointRead:
    service = EndpointService(uow)
    endpoint = await service.get(endpoint_id, auth.tenant.id)
    return EndpointRead.from_domain(endpoint)


@router.patch(
    "/{endpoint_id}",
    response_model=EndpointRead,
    responses=problem_responses(401, 403, 404, 422),
    summary="Update an endpoint",
    description="Only the fields present in the request body are changed.",
)
async def update_endpoint(
    endpoint_id: uuid.UUID,
    body: EndpointUpdate,
    auth: AuthContext = Depends(_require_write),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> EndpointRead:
    service = EndpointService(uow)
    updated = await service.update(
        endpoint_id,
        auth.tenant.id,
        url=str(body.url) if body.url is not None else None,
        subscribed_event_types=(
            frozenset(body.subscribed_event_types)
            if body.subscribed_event_types is not None
            else None
        ),
        status=body.status,
    )
    return EndpointRead.from_domain(updated)


@router.delete(
    "/{endpoint_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=problem_responses(401, 403, 404),
    summary="Delete an endpoint",
)
async def delete_endpoint(
    endpoint_id: uuid.UUID,
    auth: AuthContext = Depends(_require_write),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> None:
    service = EndpointService(uow)
    await service.delete(endpoint_id, auth.tenant.id)
