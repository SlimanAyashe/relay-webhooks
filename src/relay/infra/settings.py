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


@lru_cache
def get_settings() -> Settings:
    return Settings()
