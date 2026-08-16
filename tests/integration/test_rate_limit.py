import uuid

from redis.asyncio import Redis

from relay.infra.rate_limit import allow


async def test_allows_requests_up_to_the_burst_capacity(stream_redis: Redis) -> None:
    tenant_id = uuid.uuid4()

    for _ in range(5):
        decision = await allow(stream_redis, tenant_id, rate_per_second=1.0, burst=5, now=1000.0)
        assert decision.allowed is True

    denied = await allow(stream_redis, tenant_id, rate_per_second=1.0, burst=5, now=1000.0)
    assert denied.allowed is False
    assert denied.retry_after_seconds > 0


async def test_bucket_refills_over_time(stream_redis: Redis) -> None:
    tenant_id = uuid.uuid4()
    for _ in range(3):
        assert (
            await allow(stream_redis, tenant_id, rate_per_second=1.0, burst=3, now=1000.0)
        ).allowed
    assert not (
        await allow(stream_redis, tenant_id, rate_per_second=1.0, burst=3, now=1000.0)
    ).allowed

    # 2 seconds later, 2 tokens have refilled at 1/sec.
    first = await allow(stream_redis, tenant_id, rate_per_second=1.0, burst=3, now=1002.0)
    second = await allow(stream_redis, tenant_id, rate_per_second=1.0, burst=3, now=1002.0)
    third = await allow(stream_redis, tenant_id, rate_per_second=1.0, burst=3, now=1002.0)

    assert first.allowed is True
    assert second.allowed is True
    assert third.allowed is False


async def test_retry_after_reflects_time_until_a_token_is_available(stream_redis: Redis) -> None:
    tenant_id = uuid.uuid4()
    assert (await allow(stream_redis, tenant_id, rate_per_second=2.0, burst=1, now=1000.0)).allowed

    denied = await allow(stream_redis, tenant_id, rate_per_second=2.0, burst=1, now=1000.0)

    assert denied.allowed is False
    assert 0.4 <= denied.retry_after_seconds <= 0.6  # 1 token needed / 2 tokens-per-sec


async def test_different_tenants_have_independent_buckets(stream_redis: Redis) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    for _ in range(3):
        assert (
            await allow(stream_redis, tenant_a, rate_per_second=1.0, burst=3, now=1000.0)
        ).allowed
    assert not (
        await allow(stream_redis, tenant_a, rate_per_second=1.0, burst=3, now=1000.0)
    ).allowed

    # tenant_a exhausting its budget must not affect tenant_b's.
    assert (await allow(stream_redis, tenant_b, rate_per_second=1.0, burst=3, now=1000.0)).allowed
