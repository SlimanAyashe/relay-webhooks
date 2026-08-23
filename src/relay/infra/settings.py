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

    # Phase 4 demo console -- see docs/adr/0006-phase-4-demo-console.md.

    # Outbound HTTP timeouts, now a settings knob rather than a module constant in
    # relay.infra.http_sender. Deliberately tighter than the plan's original "10s"
    # figure so the `slow-8s` mock receiver reliably demonstrates the timeout path (an
    # 8s response comfortably clears a 5s read timeout) -- still generous for any real
    # webhook receiver, which should answer in well under a second.
    outbound_connect_timeout_seconds: float = 5.0
    outbound_read_timeout_seconds: float = 5.0
    # Identifies the service (and a docs URL) on every outbound delivery request, so an
    # abuse report against a customer's server is traceable back to us and answerable.
    outbound_user_agent: str = "Relay-Webhooks/1.0 (+https://relay.bookr.tech/docs)"
    # Note: dispatcher_concurrency (above, Phase 2) already *is* the process-wide cap on
    # concurrent outbound deliveries across every endpoint -- one asyncio.Semaphore of
    # that size gates every delivery attempt in relay.workers.dispatcher.run_forever,
    # regardless of how many distinct endpoints are registered. Phase 4's abuse-controls
    # requirement for "a global outbound concurrency cap distinct from the per-endpoint
    # cap" is this setting; it doesn't need a second one alongside
    # dispatcher_per_endpoint_concurrency (Phase 3).

    # Event ingest rejects payloads over this size with a 413 -- closes off using the
    # public sandbox as a large-payload relay.
    event_payload_max_bytes: int = 65_536

    # Sandbox: a scoped, TTL-limited tenant + API key an interviewer can self-provision
    # from the demo console with no signup, hard-capped well below normal tenant limits.
    sandbox_ttl_minutes: int = 60
    sandbox_max_endpoints: int = 3
    sandbox_max_events: int = 20
    # Deliberately reuses relay.infra.rate_limit's token-bucket mechanism rather than a
    # second limiter -- just parameterized far tighter than a real tenant's budget.
    sandbox_rate_limit_requests_per_second: float = 1.0
    sandbox_rate_limit_burst: int = 3
    # Per-IP limit on POST /v1/sandbox itself, keyed by client IP (see
    # relay.api.v1.sandbox.router) -- prevents scripted sandbox-key farming.
    sandbox_creation_rate_limit_requests_per_second: float = 0.05
    sandbox_creation_rate_limit_burst: int = 3

    # Phase 5 observability & ops -- see docs/adr/0007-phase-5-observability-and-ops.md.

    # Port the dispatcher's own Prometheus exporter listens on. Separate from api_port
    # because the dispatcher (like the other workers) serves no HTTP of its own otherwise;
    # it's the one worker process with event-driven metrics to report (delivery outcomes,
    # breaker state, queue depth/in-flight), so it gets a minimal start_http_server() rather
    # than every worker growing an HTTP surface.
    # Sampled fresh on every dispatcher poll loop iteration (an XLEN/XPENDING call each,
    # both cheap) rather than on a separate timer -- see
    # docs/adr/0007-phase-5-observability-and-ops.md.
    dispatcher_metrics_port: int = 9100

    # Nightly backup (scripts/backup_postgres.py) and the restore drill
    # (scripts/restore_drill.py). Point at real AWS S3 in production (leave
    # backup_s3_endpoint_url unset); a local drill points it at MinIO or any other
    # S3-compatible endpoint instead -- the scripts don't change, only this URL does.
    backup_s3_bucket: str = "relay-backups"
    backup_s3_prefix: str = "postgres"
    backup_s3_endpoint_url: str | None = None
    backup_postgres_container: str = "relay-postgres-1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
