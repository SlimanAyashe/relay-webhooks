import uuid

from fastapi import APIRouter, Depends, Response, status
from redis.asyncio import Redis

from relay.api.auth import AuthContext, require_scope
from relay.api.openapi import problem_responses
from relay.api.v1.deliveries.schemas import DeliveryRead
from relay.infra.redis import get_redis
from relay.repositories.unit_of_work import UnitOfWork, get_unit_of_work
from relay.services.deliveries.replay_service import ReplayService

router = APIRouter(prefix="/v1/deliveries", tags=["deliveries"])

_require_replay = require_scope("deliveries:replay")


@router.post(
    "/{delivery_id}/replay",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DeliveryRead,
    responses=problem_responses(401, 403, 404, 409),
    summary="Replay a dead delivery",
    description=(
        "Starts a fresh delivery chain for a delivery whose retry budget was exhausted "
        "(state=dead): resets it to pending with a full retry budget and re-enqueues it. "
        "The original attempt history is never modified and stays queryable via "
        "GET /v1/dlq. 404 if the delivery doesn't exist or doesn't belong to the "
        "authenticated tenant; 409 if it isn't currently dead."
    ),
)
async def replay_delivery(
    delivery_id: uuid.UUID,
    response: Response,
    auth: AuthContext = Depends(_require_replay),
    uow: UnitOfWork = Depends(get_unit_of_work),
    redis: Redis = Depends(get_redis),
) -> DeliveryRead:
    service = ReplayService(uow, redis)
    delivery = await service.replay(delivery_id, auth.tenant.id)
    response.headers["Location"] = f"/v1/deliveries/{delivery.id}"
    return DeliveryRead.from_domain(delivery)
