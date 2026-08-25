"""Redis Pub/Sub for live delivery-attempt events -- the `W -->|publish| PS[Redis
Pub/Sub] --> SSE[SSE -> Demo Console]` edge in the architecture diagram. One channel per
tenant, not one shared channel filtered in application code: subscribing to
`{PREFIX}{tenant_id}` makes cross-tenant isolation structural (another tenant's events
physically never arrive on this subscription) rather than a filter that could be
forgotten or get it wrong.

Pub/Sub, not a Stream or a persisted table: this is a best-effort live feed for a demo
console tab that's open right now, not a durable record (delivery_attempts already is
the durable, replayable record of what happened -- see relay.repositories.delivery_attempts).
A subscriber that isn't connected when an event fires simply misses it, same as any other
Pub/Sub consumer; reconnecting just resumes watching from "now".
"""

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from datetime import datetime

from redis.asyncio import Redis

ATTEMPT_CHANNEL_PREFIX = "relay:attempts:tenant:"


@dataclass(frozen=True, slots=True)
class AttemptEvent:
    delivery_id: uuid.UUID
    endpoint_id: uuid.UUID
    event_id: uuid.UUID
    # None for an outcome that was not an attempt: a delivery deferred by an open circuit
    # breaker never built a request, so numbering it would both invent an attempt that has
    # no row in delivery_attempts and collide with the number of the real attempt before it.
    attempt_no: int | None
    delivery_state: str
    latency_ms: int
    response_status: int | None
    error_class: str | None
    next_retry_at: datetime | None
    breaker_state: str
    created_at: datetime
    request_headers: dict[str, str] | None = None

    def to_json(self) -> str:
        payload = asdict(self)
        payload["delivery_id"] = str(self.delivery_id)
        payload["endpoint_id"] = str(self.endpoint_id)
        payload["event_id"] = str(self.event_id)
        payload["next_retry_at"] = self.next_retry_at.isoformat() if self.next_retry_at else None
        payload["created_at"] = self.created_at.isoformat()
        return json.dumps(payload)


def _channel(tenant_id: uuid.UUID) -> str:
    return f"{ATTEMPT_CHANNEL_PREFIX}{tenant_id}"


async def publish_attempt_event(redis: Redis, tenant_id: uuid.UUID, event: AttemptEvent) -> None:
    await redis.publish(_channel(tenant_id), event.to_json())


async def subscribe_attempt_events(redis: Redis, tenant_id: uuid.UUID) -> AsyncIterator[str]:
    """Yields raw JSON message payloads published for `tenant_id`, forever, until the
    caller stops iterating (e.g. the SSE client disconnects). A fresh pubsub connection
    per call -- Redis Pub/Sub subscriptions are inherently per-connection.
    """
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(_channel(tenant_id))
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            yield message["data"]
    finally:
        await pubsub.unsubscribe(_channel(tenant_id))
        await pubsub.aclose()  # type: ignore[no-untyped-call]
