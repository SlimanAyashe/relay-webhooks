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

## Current status: Phase 0 — skeleton, 41/41 tickets done. Live at https://relay.bookr.tech

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

## What's next after Phase 0

Phase 1 (API + domain: tenants, API keys, endpoint CRUD, event ingest with idempotency keys) →
Phase 2 (the delivery engine: transactional outbox, Redis Streams, retry/backoff scheduler,
`XAUTOCLAIM` crash recovery) → Phase 3 (HMAC signing, SSRF guard, circuit breaker, DLQ) → Phase 4
(the public demo console + the failure-scenario test suite that proves the guarantees above are
real, not aspirational).
