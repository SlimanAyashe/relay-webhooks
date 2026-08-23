import asyncio
import uuid

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from relay.infra.http_sender import OutboundHttpResult
from relay.infra.streams import DeliveryMessage
from relay.repositories.unit_of_work import UnitOfWork
from relay.workers.dispatcher import _handle_and_ack


def _sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


class _ConcurrencyTrackingSender:
    """Records the peak number of overlapping in-flight send() calls, so the test can
    assert the per-endpoint semaphore actually bounds concurrency rather than just trusting
    the implementation.
    """

    def __init__(self, delay: float) -> None:
        self._delay = delay
        self.in_flight = 0
        self.max_in_flight = 0

    async def send(
        self, *, url: str, payload: bytes, headers: dict[str, str]
    ) -> OutboundHttpResult:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(self._delay)
        self.in_flight -= 1
        return OutboundHttpResult(latency_ms=5, status_code=200)


async def _seed_deliveries_for_one_endpoint(
    sessionmaker: async_sessionmaker[AsyncSession], count: int
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    uow = UnitOfWork(sessionmaker)
    async with uow:
        tenant = await uow.tenants.create(name=f"acme-{uuid.uuid4()}")
        endpoint = await uow.endpoints.create(
            tenant_id=tenant.id,
            url="https://slow.example.com/webhook",
            secret="s3cr3t",
            subscribed_event_types=frozenset({"order.created"}),
        )
        delivery_ids = []
        for _ in range(count):
            event = await uow.events.create(
                tenant_id=tenant.id,
                event_type="order.created",
                payload={},
                idempotency_key=str(uuid.uuid4()),
            )
            delivery = await uow.deliveries.create(event_id=event.id, endpoint_id=endpoint.id)
            delivery_ids.append(delivery.id)
        await uow.commit()
    return endpoint.id, delivery_ids


async def test_per_endpoint_semaphore_caps_concurrent_attempts_against_one_endpoint(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    """Four deliveries against the same endpoint, dispatched "concurrently" (as
    run_forever's task-per-message loop would), with plenty of global concurrency budget --
    but the per-endpoint semaphore should still cap how many of them the sender ever sees
    in flight at once, regardless of the global cap.
    """
    per_endpoint_cap = 2
    sessionmaker = _sessionmaker(db_engine)
    endpoint_id, delivery_ids = await _seed_deliveries_for_one_endpoint(sessionmaker, count=4)

    sender = _ConcurrencyTrackingSender(delay=0.05)
    global_semaphore = asyncio.Semaphore(10)  # far larger than per_endpoint_cap
    endpoint_semaphores: dict[uuid.UUID, asyncio.Semaphore] = {}

    def endpoint_semaphore(target_endpoint_id: uuid.UUID) -> asyncio.Semaphore:
        sem = endpoint_semaphores.get(target_endpoint_id)
        if sem is None:
            sem = asyncio.Semaphore(per_endpoint_cap)
            endpoint_semaphores[target_endpoint_id] = sem
        return sem

    await asyncio.gather(
        *(
            _handle_and_ack(
                stream_redis,
                sender,
                DeliveryMessage(f"fake-message-{i}", delivery_id, correlation_id=None),
                global_semaphore,
                lambda: UnitOfWork(sessionmaker),
                endpoint_semaphore,
            )
            for i, delivery_id in enumerate(delivery_ids)
        )
    )

    assert sender.max_in_flight <= per_endpoint_cap
    assert len(endpoint_semaphores) == 1
    assert endpoint_id in endpoint_semaphores


async def test_without_the_endpoint_semaphore_all_four_run_concurrently(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    """Control case: with no per-endpoint gating (endpoint_semaphore=None), the global
    semaphore alone lets every message run at once -- proving the capped test above isn't
    just an artifact of how the sender happens to schedule.
    """
    sessionmaker = _sessionmaker(db_engine)
    _endpoint_id, delivery_ids = await _seed_deliveries_for_one_endpoint(sessionmaker, count=4)

    sender = _ConcurrencyTrackingSender(delay=0.05)
    global_semaphore = asyncio.Semaphore(10)

    await asyncio.gather(
        *(
            _handle_and_ack(
                stream_redis,
                sender,
                DeliveryMessage(f"fake-message-{i}", delivery_id, correlation_id=None),
                global_semaphore,
                lambda: UnitOfWork(sessionmaker),
                None,
            )
            for i, delivery_id in enumerate(delivery_ids)
        )
    )

    # Not a strict ==4: real DB round-trip timing before each send() can occasionally
    # stagger task starts by a hair. >=3 is enough to prove no low cap (like the capped
    # test's 2) is silently in effect when endpoint_semaphore is None.
    assert sender.max_in_flight >= 3
