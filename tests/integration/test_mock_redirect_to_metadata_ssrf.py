"""Testing scenario (Phase 4, backlog p4-28): the console's `redirect-to-metadata` mock
receiver (relay.api.mock.router.redirect_to_metadata), driven through the real delivery
pipeline (relay.workers.dispatcher.process_delivery_message + the real HttpxOutboundSender
adapter), is blocked by the Phase 3 SSRF guard with no connection ever made to the
metadata address -- the same assertion tests/unit/test_mock_router.py makes about the
route's response shape, now exercised end to end through a live delivery attempt.
"""

import ipaddress
import uuid

import httpx
import respx
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from relay.domain.deliveries import DeliveryState
from relay.infra.http_sender import HttpxOutboundSender
from relay.infra.ssrf_guard import Resolver
from relay.repositories.delivery_attempts.models import DeliveryAttemptModel
from relay.repositories.unit_of_work import UnitOfWork
from relay.workers.dispatcher import process_delivery_message

_PINNED_IP = "93.184.216.34"  # a fixed public (never-denied) address, never real DNS
_REDIRECT_TARGET = "http://169.254.169.254/latest/meta-data/"


def _resolver_pinning_only_the_registered_hostname(hostname: str) -> list[str]:
    """Resolves the endpoint's own hostname to the fixed public test IP, but -- like real
    getaddrinfo -- resolves an IP-literal hostname (e.g. the redirect's 169.254.169.254
    target) to itself. A resolver that blindly returned the public IP for every hostname
    would defeat the guard on the redirect hop by construction, not exercise it.
    """
    try:
        ipaddress.ip_address(hostname)
        return [hostname]
    except ValueError:
        return [_PINNED_IP]


def _sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _seed_delivery(sessionmaker: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    uow = UnitOfWork(sessionmaker)
    async with uow:
        tenant = await uow.tenants.create(name=f"acme-{uuid.uuid4()}")
        endpoint = await uow.endpoints.create(
            tenant_id=tenant.id,
            # Same shape as what an interviewer would register from the console:
            # https://<domain>/mock/redirect-to-metadata.
            url="https://relay.example.com/mock/redirect-to-metadata",
            secret="s3cr3t",
            subscribed_event_types=frozenset({"demo.triggered"}),
        )
        event = await uow.events.create(
            tenant_id=tenant.id,
            event_type="demo.triggered",
            payload={"n": 1},
            idempotency_key="i",
        )
        delivery = await uow.deliveries.create(event_id=event.id, endpoint_id=endpoint.id)
        await uow.commit()
    return delivery.id


@respx.mock
async def test_redirect_to_metadata_mock_is_blocked_end_to_end_with_no_connection_made(
    db_engine: AsyncEngine, stream_redis: Redis
) -> None:
    sessionmaker = _sessionmaker(db_engine)
    delivery_id = await _seed_delivery(sessionmaker)
    # Exactly what relay.api.mock.router.redirect_to_metadata actually returns: a 307 to
    # the real cloud metadata IP. respx registers no route for that IP at all, so if the
    # guard ever let a connection through, respx would raise AllMockedAssertionError
    # rather than silently succeeding.
    first_hop = respx.post(f"https://{_PINNED_IP}/mock/redirect-to-metadata").mock(
        return_value=httpx.Response(307, headers={"Location": _REDIRECT_TARGET})
    )
    resolver: Resolver = _resolver_pinning_only_the_registered_hostname
    sender = HttpxOutboundSender(resolver=resolver)

    await process_delivery_message(
        lambda: UnitOfWork(sessionmaker), sender, stream_redis, delivery_id
    )
    await sender.aclose()

    async with UnitOfWork(sessionmaker) as check_uow:
        delivery = await check_uow.deliveries.get(delivery_id)
        assert delivery is not None
        # Budget not exhausted after one attempt -- retrying, not dead.
        assert delivery.state is DeliveryState.RETRYING

        attempts = await check_uow._session.execute(
            select(DeliveryAttemptModel).where(DeliveryAttemptModel.delivery_id == delivery_id)
        )
        rows = list(attempts.scalars())
        assert len(rows) == 1
        assert rows[0].error_class == "ssrf_blocked"
        assert rows[0].response_status is None

    assert first_hop.call_count == 1  # the redirect response itself was real
    assert respx.calls.call_count == 1  # nothing else -- specifically not the metadata IP
