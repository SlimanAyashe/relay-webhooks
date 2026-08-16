from fastapi import APIRouter, Depends, Query

from relay.api.auth import AuthContext, require_scope
from relay.api.openapi import problem_responses
from relay.api.v1.dlq.schemas import DeadDeliveryRead, DlqListResponse
from relay.repositories.unit_of_work import UnitOfWork, get_unit_of_work
from relay.services.deliveries.dlq_service import DlqService

router = APIRouter(prefix="/v1/dlq", tags=["dlq"])

_require_read = require_scope("dlq:read")


@router.get(
    "",
    response_model=DlqListResponse,
    responses=problem_responses(401, 403, 422),
    summary="List dead deliveries",
    description=(
        "Cursor-paginated list of the tenant's dead-lettered deliveries (retry budget "
        "exhausted), each with its full attempt history attached. Replay one via "
        "POST /v1/deliveries/{id}/replay."
    ),
)
async def list_dead_deliveries(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(_require_read),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> DlqListResponse:
    service = DlqService(uow)
    page = await service.list_dead(auth.tenant.id, cursor=cursor, limit=limit)
    return DlqListResponse(
        items=[DeadDeliveryRead.from_detail(detail) for detail in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )
