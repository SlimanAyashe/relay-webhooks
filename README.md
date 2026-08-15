# Relay

A webhook delivery service: tenants register HTTPS endpoints, subscribe to event types, and POST
events; Relay durably fans them out with at-least-once delivery, HMAC signing, jittered retry
backoff, per-endpoint circuit breakers, and a replayable dead-letter queue.

See [RELAY-PLAN.md](../RELAY-PLAN.md) for the full architecture and build phases.

**Status: Phase 0 — skeleton.** Right now this is a booting FastAPI app wired to Postgres and
Redis with `/healthz` and `/readyz`, real CI, and a hardened Docker image — no event ingest or
delivery engine yet. That lands in Phases 1–2.

## Architecture (current)

`api` (FastAPI) talks to `infra` (SQLAlchemy async engine, Redis client, Settings) directly for
now, since there's no business logic yet to justify a `services`/`repositories` layer in between
— `import-linter` already enforces the intended `api → services → repositories → infra` layering
in CI so the boundary can't erode once real logic lands. `/healthz` is a liveness check with no
dependencies; `/readyz` checks Postgres and Redis independently and returns a per-dependency
breakdown, which is what the deploy pipeline polls after every swap. Locally, Caddy reverse-proxies
to the API over plain HTTP (`docker/compose.yml`). In production, TLS and routing are handled by a
Traefik instance already running on the deploy VPS (shared with other services on that box) via
Docker labels on the `api` container — see `docs/adr/0002-shared-vps-traefik.md` for why that
differs from the local setup.

## Local setup

```bash
uv sync              # install deps into .venv
cp .env.example .env # see below for what each var does
make up               # docker compose: postgres, redis, api, caddy
make migrate          # alembic upgrade head
make test             # unit + integration (integration spins up its own
                       # throwaway Postgres/Redis via testcontainers)
make lint              # ruff check + format --check
make typecheck          # mypy src
```

Open `http://localhost:8000/docs` for the interactive OpenAPI, or `http://localhost:8080/readyz`
to hit the same API through the local Caddy proxy.

## Environment variables

Documented in [.env.example](.env.example) — copy it to `.env` before running anything. Summary:

| Variable | Purpose |
| --- | --- |
| `ENV` | Deployment environment label (`local`, `ci`, `staging`, `production`) |
| `LOG_LEVEL` | Log verbosity |
| `DATABASE_URL` | Async Postgres connection string (`asyncpg` driver) |
| `REDIS_URL` | Redis connection string |
| `API_PORT` | Port the API listens on inside its container |

## Docker image

Multi-stage build (`docker/Dockerfile`): a `uv`-based builder stage produces a `.venv`, the
runtime stage copies only that venv plus `src/`, runs as a non-root user, supports a read-only
root filesystem, and pins its base image by digest.

```bash
docker build -f docker/Dockerfile -t relay:latest .
```

Final image size: **315MB** (`python:3.12-slim` base).

## Known limitations (Phase 0)

- No event ingest, delivery engine, signing, or SSRF guard yet — Phase 0 only proves the skeleton
  deploys; see [RELAY-PLAN.md](../RELAY-PLAN.md) for what's next.
- CI workflow (`.github/workflows/ci.yml`) is written and locally validated but has not yet run
  against a live GitHub Actions runner — that requires pushing this repo to GitHub.
- Production deploy is tag-triggered (`vX.Y.Z`) and not yet exercised end-to-end — see
  `docs/PROJECT_STATUS.md` for current status.
