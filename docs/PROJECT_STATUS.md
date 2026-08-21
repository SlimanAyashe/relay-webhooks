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

Phase 0 (skeleton), Phase 1 (API and domain), and Phase 2 (the delivery engine) are complete in
code and **deployed to production** as `v0.1.7` — `api`, `relay-worker`, `dispatcher`,
`scheduler`, and `reaper` all running, `/readyz` green (Postgres + Redis both `ok`). See "Phase 1
deploy, 2026-08-15/16" under Phase 2 below for what the deploy actually took (three unrelated
bugs, none of them in the application code).

Phase 3 (security and resilience — HMAC signing, the SSRF guard incl. the IP-pinned transport,
per-endpoint circuit breaker, DLQ + replay endpoint, per-tenant rate limiting) is **complete in
code on `feat/phase-3-security-resilience`, not yet merged to `main` or deployed**. See "Phase 3
— security and resilience" below for what's built and what's deliberately deferred.

Phase 4 (the public demo console -- mock receivers, self-serve sandbox provisioning, the SSE
attempt timeline, signature verifier, DLQ replay UI, metrics strip, and the abuse controls that
make a public outbound HTTP proxy safe to expose) is **complete in code, same branch as Phase 3,
not yet merged or deployed**. See "Phase 4 — the demo console" below.

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
- `deploy.yml` now `scp`s `docker/compose.prod.yml` to the VPS on every deploy, not just
  `deploy_remote.sh` — it used to only copy the script, so the VPS's compose file silently
  drifted from the repo (see "Phase 1 + 2 deploy" under Phase 2 below). Never edit
  `/opt/relay/docker/compose.prod.yml` directly on the VPS; it gets overwritten on the next
  deploy.
- Never run `docker/compose.yml` (the local-dev file, no suffix) against the VPS, even by hand —
  its `caddy` service fights the shared Traefik for :80/:443 and the whole stack gets torn down.
  Only `compose.prod.yml`, only via `deploy_remote.sh`.

## Phase 1 — API and domain (complete, deployed)

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

## Phase 2 — delivery engine (complete, deployed)

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

**Phase 1 + 2 deploy, 2026-08-15/16 — what it actually took:** the tag-push flow needed three
rounds to reach a green `/readyz`, none of them application bugs:

1. Before this deploy was even attempted, a stray manual `docker compose -f docker/compose.yml
   up -d` (the *local dev* file, which includes Caddy) had been run directly against the prod
   `relay_default` network. Caddy tried to bind :80/:443, which the shared Traefik already owns,
   so the stack got torn down again — leaving `relay-api-1` and all four workers gone entirely
   (Postgres/Redis, defined identically in both compose files, were left running throughout).
   That's why prod was already 404-ing on every route before any new tag went out. Takeaway:
   never run `docker/compose.yml` (no suffix) against the VPS — only `compose.prod.yml`, and only
   via `deploy_remote.sh`.
2. `v0.1.6` failed at the migration step: `password authentication failed for user "relay"`.
   `/opt/relay/.env` carries `POSTGRES_PASSWORD` *and* a separately-typed `DATABASE_URL` (the
   latter added for the standalone `alembic upgrade head` step — see the Phase 0 gotchas above);
   the two had drifted out of sync, almost certainly from one of the several failed bootstrap
   attempts during Phase 0 provisioning (`v0.1.0`–`v0.1.2` in the deploy history all failed).
   Fixed by reading the *actual* password already baked into the running `relay-postgres-1`
   container's own environment (the value real Postgres actually accepted at `initdb`) and
   resyncing both `.env` keys to it — not by guessing or regenerating a new password, which would
   have needed a Postgres-side `ALTER ROLE` too.
3. `v0.1.7`'s first attempt failed with `no such service: relay-worker`. Cause: `deploy.yml` only
   ever `scp`s `deploy_remote.sh` to the VPS — never `docker/compose.prod.yml`. That file only
   reaches `/opt/relay/docker/` via one-off manual provisioning, so it had silently stayed on the
   pre-Phase-2 version (no worker services) the whole time the repo's copy moved on. Fixed by
   `scp`-ing the compose file alongside the script on every deploy from now on (see `deploy.yml`);
   also had to manually sync the VPS's copy once to unblock this deploy itself.

None of the three would have been caught by CI — they're all VPS-state problems, invisible from
the repo. Worth a skim before the next deploy in case any of the same drift has crept back.

## Phase 3 — security and resilience (complete in code, not yet deployed)

Phase 3's goal: turn Phase 2's delivery engine, which would call whatever URL an endpoint has
with no signing and no breaker, into something safe to eventually put behind a public demo
console — HMAC-signed payloads, an SSRF guard with a DNS-rebinding-resistant IP-pinned
transport, a per-endpoint circuit breaker, a real DLQ (retry-budget exhaustion actually
transitions a delivery to `dead`, and it's replayable), and per-tenant rate limiting.

**Done:**
- One new Alembic migration (`consecutive_failures` integer, default 0, on `endpoints` — the
  breaker's failure count needed a durable, per-endpoint home); `breaker_state`/`opened_at`
  already existed on that table from Phase 2's schema, unused until now
- `relay.infra.signing`: pure HMAC-SHA256 signer/verifier (`sign()`/`verify()`, `hmac.compare_digest`,
  a configurable replay-tolerance window), wired into `DeliveryAttemptService.attempt()` so every
  outbound request carries `X-Relay-Signature`/`X-Relay-Timestamp`/`X-Relay-Delivery-Id`
- `relay.infra.ssrf_guard` (post-resolution CIDR checks against loopback/RFC1918/link-local/CGNAT
  (100.64.0.0/10)/IPv6 ULA/the cloud metadata IP, plus a port allow-list) and
  `relay.infra.pinned_transport.PinnedAsyncTransport` (a custom httpx transport connecting
  directly to the address the guard validated, preserving `Host` and TLS SNI) — both live inside
  `HttpxOutboundSender`, which now runs the guard before every attempt and every redirect hop
  (redirects are never auto-followed), classifying a blocked destination as
  `AttemptErrorClass.SSRF_BLOCKED` rather than a generic connection failure
- `relay.domain.endpoints.breaker`: pure closed/open/half-open transition logic (5 consecutive
  failures to open by default, 60s cooldown, exactly one half-open probe), persisted atomically
  alongside the delivery-attempt write via new `EndpointRepository.record_delivery_outcome()`/
  `set_breaker_state()` methods so a concurrent breaker-state read is never stale; wired into
  `DeliveryAttemptService.attempt()`, which skips the HTTP call entirely (and defers via the new
  `DeliveryRepository.reschedule()`, which doesn't spend the delivery's retry-attempt budget)
  while the breaker is open and cooling down
- Retry-budget exhaustion is real: `DeliveryRepository.mark_dead()` transitions a delivery to
  `DeliveryState.DEAD` once `attempt_count` reaches `settings.delivery_max_attempts` (8 by
  default) instead of retrying forever
- DLQ surfaced over HTTP: `GET /v1/dlq` (new `DeliveryRepository.list_dead()`, keyset-paginated,
  scoped to tenant via a join through `events`) lists dead deliveries with their full attempt
  history attached; `POST /v1/deliveries/{id}/replay` (new `ReplayService`) resets a dead
  delivery to a fresh chain and re-publishes it to the Redis stream, leaving the original
  `delivery_attempts` rows untouched and queryable
- Redis-backed per-tenant token-bucket rate limiter (`relay.infra.rate_limit`, one atomic Lua
  script per check, same correctness shape as the retry ZSET's pop-due script), wired into
  `POST /v1/events`; a rejection raises the new `RateLimitExceeded` domain error, mapped to `429`
  with a `Retry-After` header via an extended `_problem_response()` (now accepts arbitrary
  headers, not just events-specific ones); `SsrfBlocked` also added to the domain error hierarchy
  for completeness even though nothing currently raises it across the API boundary
- Per-endpoint concurrency cap in the dispatcher: a lazily-created `asyncio.Semaphore` per
  endpoint id (capacity 3 by default), so one slow-but-still-200 destination can't consume the
  whole worker pool's concurrency budget even when the breaker hasn't tripped
- `hypothesis` added as a dev dependency for the signing property tests (payload tampering,
  timestamp-tolerance rejection); `tests/integration/test_http_sender_ssrf.py` covers the
  redirect-to-forbidden-IP and DNS-rebinding scenarios with respx-mocked destinations and fake
  resolvers, never real network I/O or real DNS
- `docs/adr/0005-phase-3-security-resilience.md`, `docs/guarantees.md` (new), `docs/runbook.md`
  (new — deploy/rollback/DLQ-drain plus the VPS egress-firewall rules documented as
  defense-in-depth, not applied to any real infrastructure), and new rows in
  `docs/failure-modes.md` for breaker trip/recovery, SSRF-blocked redirects, DNS rebinding,
  rate-limit rejection, retry-budget exhaustion, and DLQ replay
- 76 new tests (unit + integration via testcontainers, respx, freezegun, hypothesis), bringing the
  suite to 238 total, all passing; `make lint`, `make typecheck`, `uv run lint-imports`, and
  `uv run pip-audit` all clean

**Not done in Phase 3** (by design, deferred): the VPS egress firewall rules are documented but
not applied to the real, shared production VPS (see `docs/runbook.md` for why — that host is
shared with other pre-existing services and touching its firewall state needs deliberate
coordination, not something this repo's tooling should do unilaterally); no nightly backup/restore
(Phase 5, optional); no demo console yet to actually exercise any of this publicly (Phase 4).

## Phase 4 — the demo console (complete in code, not yet deployed)

Phase 4's goal: make everything Phase 1-3 built actually visible, in under a minute, to
someone with no account and no context -- and do it without weakening any guarantee Phase
3 just finished establishing, since this is the first time the service is reachable by
someone who isn't a trusted tenant. Per `docs/adr/0006-phase-4-demo-console.md`, the
console is server-rendered (Jinja2 + htmx + Alpine.js + vanilla JS for `EventSource`, no
SPA build step) and is otherwise just a browser client of the real `/v1` API plus the new
`/v1/sandbox` surface -- no console-only backend logic exists outside the mock receivers,
sandbox provisioning/quotas, and the SSE/metrics endpoints.

**Done:**
- `relay.web`: Jinja2 templates + a static-file mount (`/static`), served alongside the
  `/v1` routers by the same FastAPI app; the console itself lives at `/` and is excluded
  from the OpenAPI schema
- Five built-in mock receivers under `/mock/*` (not versioned, same reasoning as
  `/healthz`): `always-200`, `always-500`, `slow-8s`, `flaky-50`, and
  `redirect-to-metadata` (a real 307 to `169.254.169.254`, for demonstrating the Phase 3
  SSRF guard blocking a live redirect hop with no connection ever made)
- Sandbox tenant/key provisioning (`POST /v1/sandbox`, `relay.services.sandbox.service.SandboxService`):
  reuses Phase 1's API-key issuance and Phase 3's rate limiter rather than parallel
  machinery -- a sandbox tenant is a `tenants` row with a new `is_sandbox` flag, a sandbox
  key is an `api_keys` row with a new `expires_at` column (every other key's is `NULL` and
  never expires), checked alongside the existing revocation check in `relay.api.auth`.
  Rate-limited per client IP on creation itself, reusing `relay.infra.rate_limit`'s
  token-bucket keyed on a `uuid5` of the IP rather than a tenant id
- Fixed sandbox quotas -- 60-minute TTL, max 3 endpoints, max 20 events, a tighter
  per-second rate limit than a normal tenant's default -- enforced server-side
  independent of what the console UI sends. The two count caps are new
  (`relay.domain.sandbox.quota.check_quota`, a pure decision function in the same shape as
  the circuit breaker's), wired in as small router dependencies
  (`_quota_checked_write_auth`, the extended `_rate_limited_auth`) rather than changes to
  `EndpointService`/`EventIngestService` themselves -- a normal tenant is completely
  unaffected
- Live delivery-attempt timeline: the dispatcher (via `DeliveryAttemptService`, now
  optionally `redis`-aware) publishes every attempt outcome -- including a breaker-open
  deferral -- to a per-tenant Redis Pub/Sub channel (`relay.infra.attempt_events`);
  `GET /v1/sandbox/stream` is a Server-Sent Events endpoint subscribing to exactly that
  tenant's channel, so cross-tenant isolation is structural, not a filter. Because a
  browser's native `EventSource` can't set headers, this one route accepts the sandbox key
  as `?api_key=` (a documented, narrowly-scoped tradeoff -- see the ADR) via a new
  `require_scope_allow_query_key` auth dependency; every other route is unaffected
- Outbound-request inspector: `delivery_attempts` gained a `request_headers` JSONB column
  (the real `X-Relay-Signature`/`X-Relay-Timestamp`/`X-Relay-Delivery-Id` sent, not a
  client-side reconstruction), surfaced on both the DLQ listing and the live SSE feed
- `POST /v1/sandbox/verify-signature`: a thin wrapper over the real `relay.infra.signing.verify()`,
  so the console's tamper-and-fail demo exercises the actual verifier, not a JS
  reimplementation of it
- `GET /v1/sandbox/metrics`: queue depth, in-flight, p95 latency, and success rate over a
  bounded recent attempt sample, computed on request from existing tables
  (`relay.services.deliveries.metrics_service`) rather than standing up Phase 5's
  Prometheus/Grafana stack early
- DLQ replay wired into the console UI against the existing (unchanged)
  `POST /v1/deliveries/{id}/replay` from Phase 3
- Additional abuse controls: a 64 KB max event-payload size (413, checked at the ASGI
  layer via `Content-Length` and re-checked against the actual received bytes), a fixed
  identifying `User-Agent` on every outbound delivery request, and outbound
  connect/read timeouts moved from hardcoded 10s module constants to a `Settings` knob
  defaulting to 5s each -- a real (documented, deliberate) behavior change to the delivery
  engine, made so the `slow-8s` mock receiver actually demonstrates the timeout path it's
  named for. The process-wide dispatcher concurrency cap Phase 4's plan called for already
  existed since Phase 2 (`dispatcher_concurrency`); no second mechanism was added
- `docs/adr/0006-phase-4-demo-console.md`, new rows in `docs/failure-modes.md` and
  `docs/guarantees.md` for sandbox TTL/quota/isolation and the SSE query-param tradeoff
- 32 new tests (unit + integration via testcontainers, respx, freezegun), bringing the
  suite to 270 total, all passing; `make lint`, `make typecheck`, `uv run lint-imports`,
  and `uv run pip-audit` all clean; `docker build` and a real `docker compose up` smoke
  test (sandbox provisioning, endpoint registration, DLQ/metrics/SSE/verify-signature all
  exercised against the live containerized stack) both verified manually

**Not done in Phase 4** (by design, deferred): the metrics strip's "in-flight" number is
an approximation (deliveries currently `RETRYING`, not a literal count of attempts
executing this instant -- there's no live registry for that); no Prometheus/Grafana (Phase
5, optional); the SSE query-param auth tradeoff is accepted only for that one route, not
generalized.

## What's next

Phase 4 was the last **core** phase (weeks 1-8 per the project plan) -- the project is now
complete and demoable on its own. Everything remaining (Phase 5 observability/ops, Phase 6
load-test/proof/polish, Phase 7 buffer/interview-prep) is optional per the plan's own scope
discipline and can be picked up, reordered, or dropped without leaving a hole. Immediate
next steps if continuing: merge `feat/phase-3-security-resilience` (which now also carries
Phase 4) to `main` and deploy, since nothing in Phases 3-4 has reached production yet.
