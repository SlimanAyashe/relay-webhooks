import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from relay.api.auth import AuthContext, require_scope
from relay.api.v1.endpoints.schemas import (
    EndpointCreate,
    EndpointCreated,
    EndpointListResponse,
    EndpointRead,
    EndpointUpdate,
)
from relay.repositories.unit_of_work import UnitOfWork, get_unit_of_work
from relay.services.endpoints.service import EndpointService

router = APIRouter(prefix="/v1/endpoints", tags=["endpoints"])

_NOT_FOUND_DETAIL = "endpoint not found"
_require_read = require_scope("endpoints:read")
_require_write = require_scope("endpoints:write")


@router.post("", status_code=status.HTTP_201_CREATED, response_model=EndpointCreated)
async def create_endpoint(
    body: EndpointCreate,
    auth: AuthContext = Depends(_require_write),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> EndpointCreated:
    service = EndpointService(uow)
    try:
        endpoint = await service.register(
            auth.tenant.id, str(body.url), frozenset(body.subscribed_event_types)
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return EndpointCreated.created_from_domain(endpoint)


@router.get("", response_model=EndpointListResponse)
async def list_endpoints(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(_require_read),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> EndpointListResponse:
    service = EndpointService(uow)
    try:
        page = await service.list(auth.tenant.id, cursor=cursor, limit=limit)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return EndpointListResponse(
        items=[EndpointRead.from_domain(endpoint) for endpoint in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.get("/{endpoint_id}", response_model=EndpointRead)
async def get_endpoint(
    endpoint_id: uuid.UUID,
    auth: AuthContext = Depends(_require_read),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> EndpointRead:
    service = EndpointService(uow)
    try:
        endpoint = await service.get(endpoint_id, auth.tenant.id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL) from exc
    return EndpointRead.from_domain(endpoint)


@router.patch("/{endpoint_id}", response_model=EndpointRead)
async def update_endpoint(
    endpoint_id: uuid.UUID,
    body: EndpointUpdate,
    auth: AuthContext = Depends(_require_write),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> EndpointRead:
    service = EndpointService(uow)
    try:
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
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return EndpointRead.from_domain(updated)


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(
    endpoint_id: uuid.UUID,
    auth: AuthContext = Depends(_require_write),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> None:
    service = EndpointService(uow)
    try:
        await service.delete(endpoint_id, auth.tenant.id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL) from exc
