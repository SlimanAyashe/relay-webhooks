from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "local"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://relay:relay@localhost:5432/relay"
    redis_url: str = "redis://localhost:6379/0"

    api_port: int = 8000

    # Delivery engine worker tuning (Phase 2). Defaults favor a demo-friendly quick feedback
    # loop over production throughput -- see docs/adr/0004-phase-2-delivery-engine.md.
    outbox_poll_interval_seconds: float = 1.0
    outbox_claim_batch_size: int = 50
    dispatcher_concurrency: int = 10
    scheduler_tick_interval_seconds: float = 1.0
    reaper_tick_interval_seconds: float = 30.0
    reaper_min_idle_ms: int = 30_000

    # Phase 3 security & resilience tuning -- see docs/adr/0005-phase-3-security-resilience.md.
    # The SSRF-guard deny list itself (loopback/RFC1918/link-local/CGNAT/IPv6 ULA/metadata
    # IP) is NOT here -- those are fixed security invariants implemented as code constants
    # in relay.infra.ssrf_guard, not something a deployer should be able to weaken via env
    # var. Only the allowed-port set is configurable.

    # How far a signed request's timestamp may drift from "now" (either direction) and
    # still verify -- bounds the replay window.
    signature_tolerance_seconds: int = 300
    # Consecutive attempt failures before a per-endpoint circuit breaker opens.
    breaker_failure_threshold: int = 5
    # How long an open breaker waits before allowing one half-open probe attempt.
    breaker_cooldown_seconds: float = 60.0
    # Destination ports the SSRF guard allows outbound requests to target.
    ssrf_allowed_ports: frozenset[int] = frozenset({80, 443})
    # Attempts (including the first) a delivery gets before it's given up on and moved to
    # DeliveryState.DEAD -- the retry budget behind the DLQ.
    delivery_max_attempts: int = 8
    # Max concurrent in-flight delivery attempts against any single endpoint, regardless of
    # overall dispatcher concurrency -- keeps one dead/slow destination from starving the
    # worker pool.
    dispatcher_per_endpoint_concurrency: int = 3
    # Per-tenant token-bucket rate limit on event ingest: steady-state requests/sec...
    rate_limit_requests_per_second: float = 5.0
    # ...and the bucket's burst capacity (max requests admitted instantaneously).
    rate_limit_burst: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
