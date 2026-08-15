# Project status

A living handoff doc — what's built, what's decided, what's blocked, and what's next. Update it
at phase boundaries (or whenever picking this up in a new environment) rather than relying on
chat history or local tooling that doesn't travel with the repo.

## What this is

Relay is a webhook delivery service: tenants register HTTPS endpoints, subscribe to event types,
and POST events; Relay durably fans them out with at-least-once delivery, HMAC signing, jittered
retry backoff, per-endpoint circuit breakers, and a replayable dead-letter queue. Architecture,
guarantees, and the full build-phase breakdown live in the project plan (kept outside this repo —
ask whoever's driving the project for it if you need the full spec beyond what's summarized here).

## Current status: Phase 0 — skeleton, 38/41 tickets done

Phase 0's goal: an empty-but-real skeleton deployed to production before any feature work starts,
so every later phase ships into a stack already proven to deploy.

**Done:**
- Repo skeleton (`src/relay/{api,domain,services,repositories,workers,infra,web}`, `tests/`,
  `docs/adr/`), `uv`-managed, ruff + mypy (strict) + pre-commit, conventional commits enforced
- FastAPI app: `/healthz` (liveness), `/readyz` (checks Postgres + Redis independently, returns a
  per-dependency breakdown on 503)
- SQLAlchemy async engine/session, Alembic-only migrations (no `Base.metadata.create_all`
  anywhere — enforced by an integration test), Redis client
- Hardened multi-stage Dockerfile: non-root, read-only rootfs, digest-pinned base, `HEALTHCHECK`
- `docker compose` stack (Postgres + Redis + api + Caddy), verified end-to-end locally
- Full CI (`.github/workflows/ci.yml`): ruff/mypy, import-linter (architecture contracts: `api →
  services → repositories → infra`, `domain` depends on nothing), pip-audit, pytest (unit +
  integration via testcontainers — real Postgres/Redis, never mocked/SQLite), Alembic
  upgrade/downgrade round-trip, Docker build validation, build+push to GHCR on merge to `main`
- Branch protection on `main`: the 6 PR-facing CI checks required, no review requirement (solo
  repo — a required-review rule would just block the owner's own merges), force-push/delete
  disabled
- Deploy workflow (`.github/workflows/deploy.yml`, tag-triggered) + health-gated swap script
  (`scripts/deploy_remote.sh`): pulls the new image, migrates, swaps, polls `/readyz`, and rolls
  back to the previous image (leaving it running) if it never goes green — see
  `docs/adr/0001-phase-0-skeleton.md` for why this is abort-and-rollback rather than full
  deploy automation

**Blocked — needs real infrastructure that didn't exist yet when this was written:**

| Ticket | Needs | Unblocks when |
| --- | --- | --- |
| Provision VPS (Docker, firewall, DNS record) | An actual VPS + a registered domain | You have SSH access to a box and a domain's A record pointed at it |
| Add GitHub Actions deploy secrets | The VPS above | Create a deploy SSH keypair, add `DEPLOY_SSH_KEY` / `DEPLOY_HOST` / `DEPLOY_USER` as repo secrets |
| Configure production Caddy for real domain TLS | The real domain name | Write `docker/Caddyfile.prod` (referenced but not yet created — see `docker/compose.prod.yml`) with the real domain, ACME email |
| Ship first production deploy | All of the above | Push a `vX.Y.Z` tag once secrets + Caddyfile.prod exist |

If you're reading this **from the VPS itself**, the first two rows are probably already solved by
virtue of being here — the remaining work is mostly wiring the domain into
`docker/Caddyfile.prod` and adding the three deploy secrets to the GitHub repo.

## Known gotchas

- **GHCR image tags must be lowercase.** `${{ github.repository }}` preserves GitHub's actual
  casing (this repo is `SlimanAyashe/relay-webhooks`), and Actions expressions have no
  `toLower()`. Both `ci.yml` and `deploy.yml` normalize it via a shell step into `$GITHUB_ENV`
  (`IMAGE_REPO`) before it's used in any `ghcr.io/...` tag. If you add a new workflow that
  references the image, reuse `env.IMAGE_REPO`, don't re-interpolate `github.repository` raw.
- `docker/compose.prod.yml` expects `docker/Caddyfile.prod` and a `$RELAY_IMAGE` env var; neither
  exists yet (see the blocked-tickets table above).

## What's next after Phase 0

Phase 1 (API + domain: tenants, API keys, endpoint CRUD, event ingest with idempotency keys) →
Phase 2 (the delivery engine: transactional outbox, Redis Streams, retry/backoff scheduler,
`XAUTOCLAIM` crash recovery) → Phase 3 (HMAC signing, SSRF guard, circuit breaker, DLQ) → Phase 4
(the public demo console + the failure-scenario test suite that proves the guarantees above are
real, not aspirational).
