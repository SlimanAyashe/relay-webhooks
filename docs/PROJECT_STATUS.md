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

## Current status

Phase 0 (skeleton) is complete and deployed to production. Phase 1 (API and domain) is complete
in code — 34/34 tickets, fully tested against real Postgres — but **not yet deployed**; the VPS
is still running whatever Phase 0 image was last shipped. Phase 2 (the delivery engine) is
complete in code — 34/34 tickets, 162 tests passing (unit + integration against real Postgres and
Redis, including all seven Phase-2-relevant crash/retry scenarios from the plan's testing
section) — also **not yet deployed**, though the production deploy config (`compose.prod.yml`,
`deploy_remote.sh`) is now updated to run the four new worker containers. Deploying Phase 1 and
Phase 2 together is a deliberate next action, not a side effect of this work landing on `main`;
see "What's next" below.

## Phase 0 — skeleton (complete, deployed)

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

**Done (2026-08-15, from the VPS itself):**
- VPS is `srv1737964.hstgr.cloud` (Hostinger, `82.29.172.127`) — already had Docker and was
  already running other production services (`bookr.tech`, an n8n instance) behind a
  panel-managed Traefik container that owns ports 80/443. Relay joins that Traefik via Docker
  labels rather than running its own Caddy in production — see
  `docs/adr/0002-shared-vps-traefik.md` for why, which supersedes the Caddy-in-prod part of ADR
  0001. `docker/Caddyfile.prod` is intentionally never created; Caddy remains local-dev-only.
- Domain: `relay.bookr.tech` and `api.relay.bookr.tech` (subdomains of the owner's existing
  `bookr.tech`, DNS via Hostinger/`dns-parking.com`), both A-recorded to `82.29.172.127` and
  confirmed propagated.
- Dedicated deploy SSH keypair generated (`~/.ssh/relay_webhooks_deploy` on the VPS, separate
  from the pre-existing `bookr_github_actions_deploy*` keys), public half in the VPS's
  `authorized_keys`. `DEPLOY_SSH_KEY` / `DEPLOY_HOST` / `DEPLOY_USER` repo secrets set
  (`DEPLOY_USER=root`, matching how the box's other deploys already run).
- `/opt/relay/docker/compose.prod.yml` + `/opt/relay/.env` (generated random
  `POSTGRES_PASSWORD`, `chmod 600`, never committed) provisioned on the VPS — this is what
  `scripts/deploy_remote.sh` runs against.

**Shipped (2026-08-15):** first production deploy is live — `v0.1.5` is running at
`https://relay.bookr.tech` / `https://api.relay.bookr.tech`, real Let's Encrypt TLS via the
shared Traefik, `/readyz` green. Phase 0 is complete.

It took a few tag pushes to get there (`v0.1.0`–`v0.1.5`); worth knowing for next time:
- The VPS had never run `docker compose up` for this project before, so `deploy_remote.sh`'s
  assumption that `relay_default`'s network/volumes already exist (it only ever does a
  component *swap*, not a first bring-up) didn't hold on a truly first deploy. Bootstrapped
  manually once: `RELAY_IMAGE=<any> docker compose --env-file .env -f docker/compose.prod.yml
  up -d postgres redis` from `/opt/relay`. Also added the missing `DATABASE_URL` to
  `/opt/relay/.env` — the standalone `docker run ... alembic upgrade head` step needs it and
  isn't covered by the compose file's own interpolation.
- `docker compose up` needs `--env-file .env` passed explicitly in `deploy_remote.sh` —
  Compose's automatic `.env`-in-cwd discovery didn't reliably pick up `POSTGRES_PASSWORD` for
  variable interpolation even with the right cwd (fixed in the script, see git history).
- An annotated tag's `github.sha` in the `deploy.yml` trigger is the *commit* it points to, not
  a separate tag-object SHA — so pushing a tag right after merging to `main` can race the
  `ci.yml` build-and-push-to-GHCR run for that same commit. Wait for the `main`-push CI run to
  finish (specifically "build + push image to GHCR") before tagging.
- Cutting several tags in quick succession (a handful of new SSH connections from different
  GitHub Actions runner IPs within ~15 minutes) tripped some network-level protection on the
  VPS — one deploy attempt's SSH connection never reached `sshd` at all (no log entry, not an
  auth failure). A ~2.5 minute pause before the next attempt cleared it. If deploys start
  mysteriously timing out on the SSH step during a burst of retries, that's probably it.

## Known gotchas

- **GHCR image tags must be lowercase.** `${{ github.repository }}` preserves GitHub's actual
  casing (this repo is `SlimanAyashe/relay-webhooks`), and Actions expressions have no
  `toLower()`. Both `ci.yml` and `deploy.yml` normalize it via a shell step into `$GITHUB_ENV`
  (`IMAGE_REPO`) before it's used in any `ghcr.io/...` tag. If you add a new workflow that
  references the image, reuse `env.IMAGE_REPO`, don't re-interpolate `github.repository` raw.
- `docker/compose.prod.yml` expects a `$RELAY_IMAGE` env var (set by `scripts/deploy_remote.sh`
  on each deploy) and a `POSTGRES_PASSWORD` in `/opt/relay/.env` on the VPS. It no longer
  expects `docker/Caddyfile.prod` — production TLS/routing is Traefik labels now, not Caddy; see
  `docs/adr/0002-shared-vps-traefik.md`.
- This VPS is **shared** with other production services (`bookr.tech`, n8n) that predate Relay.
  Don't touch the host's `traefik-traefik-1` container, `/docker/traefik/`, or `ufw`/firewall
  state without checking — those are shared blast radius, not Relay-owned.
- The CI job added in Phase 1 (`openapi-drift`) brings the required-checks count to 7; branch
  protection's required-checks list on GitHub still only names the original 6 and needs updating
  to match, or `openapi-drift` won't actually gate merges despite running.

## Phase 1 — API and domain (complete, not yet deployed)

Phase 1's goal: tenants, API keys, endpoint CRUD, and event ingest with idempotency keys, fully
layered (`api -> services -> repositories -> infra`, domain independent of all four) and tested
against real Postgres — no feature work in Phase 2+ until this foundation is solid.

**Done:**
- Four tables (`tenants`, `api_keys`, `endpoints`, `events`) via hand-reviewed autogenerated
  Alembic migrations; `UNIQUE(tenant_id, idempotency_key)` enforced at the DB level, not just in
  application code
- Three-model-set layering end to end: `relay.domain` (frozen dataclass entities, zero I/O),
  `relay.repositories` (SQLAlchemy 2.0 ORM models + repositories returning domain entities,
  never ORM objects), `relay.api.v1` (Pydantic wire schemas with explicit `from_domain()`
  boundary conversions) — see `docs/adr/0003-phase-1-api-domain.md`
- `UnitOfWork`: one commit/rollback boundary per service use case, fails safe (rolls back) if a
  caller forgets to call `commit()`
- API keys: salted-SHA-256 hash over the secret portion only (prefix and secret independently
  random), constant-time verification, issuance/rotation/revocation as one atomic unit-of-work
  each
- Auth dependency (`X-API-Key` header): validates the key, checks revocation, loads the owning
  tenant, enforces per-route scopes, and every downstream query is scoped to
  `AuthContext.tenant.id` — never a tenant id read from the request itself
- `GET /v1/endpoints` keyset-paginates over `(created_at, id)`, not `OFFSET`
- `POST /v1/events`: `202 Accepted` + `Location` header; identical-body replay of the same
  `Idempotency-Key` returns the original event, differing body is `409`
- RFC 9457 `application/problem+json` everywhere: one central exception hierarchy
  (`relay.domain.errors`, HTTP-agnostic by design) mapped by one handler, Pydantic 422s
  normalized into the same envelope, a `trace_id` (propagated or generated by
  `TraceIdMiddleware`) on every response and every error body, and unhandled exceptions never
  leak a stack trace across the wire
- OpenAPI carries real examples, a documented auth scheme, and every problem+json shape per
  endpoint; a CI job (`openapi-drift`) fails the build if `docs/openapi.json` drifts from the code
- `import-linter`'s domain-independence contract turned out to be a structural no-op since Phase
  0 (`type = "independence"` with a single-item list checks nothing) — fixed to `type =
  "forbidden"`, which actually enforces it; both contracts verified against a deliberately
  reintroduced violation, not just assumed correct
- 124 tests (unit + integration via testcontainers — real Postgres, never mocked/SQLite),
  `polyfactory` factories for all four entities, `docs/failure-modes.md` mapping each Phase 1
  failure mode to the specific test that proves it

**Not done in Phase 1** (by design, not oversight): no `/v1/tenants` or `/v1/api-keys` HTTP
routes — the backlog only ever specified routers for endpoints and events, so tenant/API-key
issuance has no HTTP surface yet. `ApiKeyService` exists and is fully tested; something (a future
router, or an out-of-band admin process) still needs to call it.

## Phase 2 — delivery engine (complete, not yet deployed)

Phase 2's goal: turn the durably-committed events from Phase 1 into actual outbound deliveries —
transactional outbox, Redis Streams consumer groups, jittered retry/backoff, `XAUTOCLAIM` crash
recovery — the mechanics behind the project's at-least-once delivery claim.

**Done:**
- Three tables (`outbox`, `deliveries`, `delivery_attempts`) via hand-reviewed autogenerated
  Alembic migrations; `outbox.event_id` is `UNIQUE` (one outbox row per event)
- Domain entities (`OutboxEntry`, `Delivery`, `DeliveryAttempt`) plus a pure
  `compute_backoff_seconds()` — full-jitter exponential backoff, `min(cap, base * 2^attempt)`
  bound then a uniform draw from `[0, bound)` — and matching ORM models/repositories following
  the same three-model-set separation as Phase 1
- `EventIngestService` now writes the outbox row in the *same* transaction as the event row (one
  extra line in an existing use case, not a new one), so a `202` still only follows a fully
  durable commit
- `OutboxRepository.claim_due()` uses `SELECT ... FOR UPDATE SKIP LOCKED` so concurrent relay
  instances never claim the same row twice
- `EndpointRepository.list_active_subscribed()` (new fan-out query, `tenant_id` + Postgres
  `= ANY(subscribed_event_types)`) resolves which endpoints a claimed event fans out to
- Redis Streams infra (`relay.infra.streams`): idempotent consumer-group creation, `XADD`/
  `XREADGROUP`/`XACK`/`XAUTOCLAIM` helpers; Redis ZSET retry-schedule infra
  (`relay.infra.retry_schedule`) with an atomic pop-due Lua script (`ZRANGEBYSCORE` + `ZREM` in
  one call, so two scheduler instances can't both fire the same due retry)
- Outbound HTTP as a port (`OutboundHttpSender` protocol) + a real `httpx`-based adapter
  classifying timeouts/connection errors/non-2xx into a closed `AttemptErrorClass` set, so
  `DeliveryAttemptService` never touches `httpx` exceptions directly
- Four worker processes (`relay.workers.{relay,dispatcher,scheduler,reaper}`), each with a
  `run_once`/`run_forever` split for testability, SIGTERM/SIGINT graceful drain
  (`relay.workers.shutdown`), and their own `docker-compose.yml` services (healthcheck disabled
  on all four — they don't serve HTTP, only the api service's baked-in `/healthz` check applies)
  - **relay**: claim → resolve subscribed endpoints → one `Delivery` row + one stream message per
    endpoint → mark outbox processed, all in one unit of work
  - **dispatcher**: bounded-concurrency `XREADGROUP` consumer loop; on a retryable failure,
    schedules the retry in the ZSET instead of re-queuing immediately
  - **scheduler**: tick loop moving due ZSET entries back onto the stream
  - **reaper**: `XAUTOCLAIM` sweep for stream entries a dead consumer left pending, reprocessed
    under their original message ID (shares `process_delivery_message` with the dispatcher)
- `import-linter`'s layers contract extended with `relay.workers` as a sibling of `relay.api`
  (`"relay.api | relay.workers"`), both still forbidden from `relay.domain`
- 162 tests total (unit + integration via testcontainers — real Postgres and Redis Streams
  consumer groups, `respx` for real-`httpx`-adapter classification, `freezegun` for backoff
  timing), covering all seven Phase-2-relevant scenarios from the plan's testing section
  (outbox-survives-a-pre-publish-crash, `SKIP LOCKED` race, `XAUTOCLAIM` reclaim, duplicate
  delivery after a post-send crash, jittered backoff on failure, timeout classification, ZSET-to-
  stream retry handoff) — `docs/failure-modes.md` now maps each to its proving test
- `docs/adr/0004-phase-2-delivery-engine.md`: outbox vs. direct publish, Redis Streams vs. Celery,
  full-jitter backoff, and reclaim-under-original-ID vs. republish-as-new

**Not done in Phase 2** (by design, deferred to Phase 3 per the plan): no HMAC signing on
outbound requests yet, no SSRF guard (the httpx adapter will call whatever URL an endpoint has,
including private/internal addresses — fine for now since only tenants who registered the
endpoint can trigger it, but not safe once Phase 4's public demo console lets an interviewer
paste an arbitrary URL), no circuit breaker (a dead endpoint just keeps retrying with backoff
rather than tripping), and no DLQ (`DeliveryState.DEAD` exists in the domain enum per the plan's
data model, but nothing transitions a delivery into it yet — retries currently continue
indefinitely at the capped backoff interval rather than exhausting a budget).

**Known gotchas added in Phase 2:**
- `httpx` moved from a dev-only dependency to a main one in Phase 2 (the real outbound sender
  needs it at runtime, not just in tests) — if a future phase removes the real adapter for some
  reason, check whether it should move back.

**Done (production deploy wiring, added after Phase 2 code-complete):**
`docker/compose.prod.yml` now defines `relay-worker`, `dispatcher`, `scheduler`, and `reaper`
services alongside `api` — same `$RELAY_IMAGE`, same DB/Redis env wiring, `restart:
unless-stopped`, no ports exposed (they don't serve HTTP), baked-in `HEALTHCHECK` disabled (it
curls `/healthz`, which only `api` serves). `scripts/deploy_remote.sh` now brings up all five
services together on every swap and rolls all five back together if `/readyz` never goes green —
`api`'s `/readyz` (Postgres + Redis) remains the single health gate for the whole swap, since none
of the workers have an HTTP endpoint of their own to check individually. Deploying the api image
alone (the old behavior) would have shipped the delivery-engine code with nothing running it.

## What's next

Deploy Phase 1 and Phase 2 together (tag + push, same flow as Phase 0) — the prod compose/deploy-
script gap above is closed, so this is now just the normal deploy flow. Then Phase 3 (HMAC
signing, SSRF guard incl. the IP-pinned transport, circuit breaker, DLQ + replay endpoint,
per-tenant rate limiting) → Phase 4 (the public demo console + the remaining
failure-scenario tests — SSRF-blocked redirect, breaker open/half-open/closed, revoked/malformed
API key — that prove the full guarantee set is real, not aspirational).
