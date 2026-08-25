# Relay

<!--
  Demo GIF placeholder (backlog-p6-11, not yet recorded -- requires a real screen
  capture, which is out of scope for this pass). When it exists, it belongs at:
    docs/assets/demo.gif
  and should replace this comment with:
    ![Relay demo console walkthrough](docs/assets/demo.gif)
  It should show ~20 seconds of the "Delivery Theater" console: start a sandbox,
  register the always-500 mock receiver, trigger an event, and watch the attempt
  timeline show retries, the breaker trip, and the DLQ fill.
-->

**[Demo GIF placeholder -- see `docs/assets/demo.gif` note in the source of this file. Not
recorded yet.]**

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/architecture-dark.svg">
  <img alt="Relay architecture: a tenant POSTs an event to the FastAPI ingest, which commits the event row and an outbox row in one PostgreSQL transaction; relay-worker fans that outbox row out onto a Redis stream, dispatchers sign and send each delivery through an SSRF guard to the customer endpoint, and a retry sorted set, scheduler, and reaper carry failed or orphaned work back onto the same stream." src="docs/assets/architecture-light.svg">
</picture>

<!--
  Regenerate both SVGs with:  python3 scripts/gen_architecture_diagram.py
  Edit the layout tables in that script rather than the SVGs by hand.
-->

A webhook delivery service: tenants register HTTPS endpoints, subscribe to event types, and POST
events; Relay durably fans them out with at-least-once delivery, HMAC signing, jittered retry
backoff, per-endpoint circuit breakers, and a replayable dead-letter queue.

**[docs/guarantees.md](docs/guarantees.md)** states exactly what this promises -- and exactly
where each promise stops -- and is the highest-signal page in this repo if you only read one.
[docs/failure-modes.md](docs/failure-modes.md) is the running "if it dies here, then what, and
which test proves it" log behind those promises. [docs/runbook.md](docs/runbook.md) covers
deploy, rollback, and DLQ replay. [RELAY-PLAN.md](../RELAY-PLAN.md) has the full build-phase plan.

**Status:** Phases 0–5 (skeleton, API/domain, delivery engine, security & resilience, the
public demo console, and observability/ops) are complete and **deployed** at
`https://relay.bookr.tech`, with Phase 6's load-test harness and tooling hardening on top.
Phase 8 (live verification) has since verified those guarantees *against the deployment*
rather than only in CI; see [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) for the current
state and the four things the live run found.

[docs/live-verification.md](docs/live-verification.md) explains how each guarantee is checked
against the deployed service, and [docs/failure-scenarios.md](docs/failure-scenarios.md) maps
the plan's twelve failure scenarios to the named tests that prove them.
[docs/runbook.md](docs/runbook.md) also carries a dated log of the operational drills that
have actually been executed.

## Architecture notes

`api → services → repositories → infra` (plus `workers`, a sibling of `api`, for the delivery
engine's background processes) is the enforced layering, checked by `import-linter` in CI;
`domain` depends on nothing. `/healthz` is a liveness check with no dependencies; `/readyz`
checks Postgres and Redis independently and returns a per-dependency breakdown, which is what
the deploy pipeline polls after every swap. Every process logs structured JSON to stdout
(`relay.infra.logging`) and a single correlation id ties an API request to every worker log
line produced while delivering the event it created — see
[docs/adr/0007-phase-5-observability-and-ops.md](docs/adr/0007-phase-5-observability-and-ops.md).
Locally, Caddy reverse-proxies to the API over plain HTTP (`docker/compose.yml`). In production,
TLS and routing are handled by a Traefik instance already running on the deploy VPS (shared with
other services on that box) via Docker labels on the `api` container — see
`docs/adr/0002-shared-vps-traefik.md` for why that differs from the local setup.

## Local setup

```bash
uv sync              # install deps into .venv
cp .env.example .env # see below for what each var does
make up               # docker compose: postgres, redis, api, caddy
make migrate          # alembic upgrade head
make test             # unit + integration (integration spins up its own
                       # throwaway Postgres/Redis via testcontainers)
make lint              # ruff check + format --check
make typecheck          # mypy --strict src
```

The live suites are opt-in by path, because they talk to a *running deployment* rather than to
testcontainers — a bare `pytest` never picks them up:

```bash
RELAY_E2E_BASE_URL=https://relay.bookr.tech \
RELAY_E2E_RECEIVER_BASE_URL=https://relay.bookr.tech make test-e2e   # guarantee-pinned smoke
make test-chaos        # kills real containers; run it on a stack you're watching
make verify-egress     # network-layer egress posture, from inside a worker container
```

See [docs/live-verification.md](docs/live-verification.md) for why there are two URLs and what
each suite does and doesn't prove.

Open `http://localhost:8000/docs` for the interactive OpenAPI, or `http://localhost:8080/readyz`
to hit the same API through the local Caddy proxy.

## Load testing

`tests/load/` holds a k6 harness (`events_ingest.js`) plus a fixture-provisioning script
(`scripts/load_test_setup.py`) and a matrix orchestrator (`tests/load/run_matrix.sh`) for
sweeping the plan's performance axes (worker count x concurrent destinations x destination
health). See [tests/load/README.md](tests/load/README.md) for how to run a single cell or the
full matrix, and why the setup script provisions a tenant directly rather than via the public
sandbox. Publishing the swept results in `docs/load-test-results.md` is separate, later work.

## Environment variables

Documented in [.env.example](.env.example) — copy it to `.env` before running anything. Summary:

| Variable | Purpose |
| --- | --- |
| `ENV` | Deployment environment label (`local`, `ci`, `staging`, `production`) |
| `LOG_LEVEL` | Log verbosity |
| `DATABASE_URL` | Async Postgres connection string (`asyncpg` driver) |
| `REDIS_URL` | Redis connection string |
| `API_PORT` | Port the API listens on inside its container |
| `DISPATCHER_METRICS_PORT` | Port the dispatcher's own Prometheus exporter listens on |
| `BACKUP_S3_BUCKET` / `BACKUP_S3_PREFIX` / `BACKUP_S3_ENDPOINT_URL` | Nightly backup destination (see `docs/runbook.md`) |

The full list, including Phase 2–5 worker/security/sandbox/observability tuning, is in
[.env.example](.env.example) with a comment on every variable.

## Observability

`GET /metrics` (api) and `GET :9100/metrics` (the dispatcher's own exporter) serve Prometheus
text exposition — see [docs/runbook.md](docs/runbook.md)'s Observability section for exactly
what each one carries and why there are two targets instead of one.

## Docker image

Multi-stage build (`docker/Dockerfile`): a `uv`-based builder stage produces a `.venv`, the
runtime stage copies only that venv plus `src/`, runs as a non-root user, supports a read-only
root filesystem, and pins its base image by digest.

```bash
docker build -f docker/Dockerfile -t relay:latest .
```

Final runtime image size: **320MB** (`python:3.12-slim` base), measured via `docker images`
after a clean `make build`.

## Known limitations

See [docs/guarantees.md](docs/guarantees.md)'s "Not guaranteed" section and
[RELAY-PLAN.md](../RELAY-PLAN.md)'s scope-discipline notes for the full, honest list
(no ordering, no global fairness, DNS rebinding mitigated but not eliminated, etc.). A few
worth calling out here specifically:

- The VPS egress firewall rules documented in `docs/runbook.md` as defense-in-depth behind
  the application-layer SSRF guard have not been applied to the production VPS yet (it's
  shared with other pre-existing services, so this needs deliberate coordination, not
  unilateral automation). Phase 8 measured what that costs: a worker container can open a TCP
  connection to the host's own SSH port, so the app-layer guard is currently the only thing
  stopping it.
- `GET /metrics` is unauthenticated and not restricted at the network layer in production —
  same reasoning and same "not this repo's tooling's call to make unilaterally" as the
  egress-firewall rules above.
- The nightly-backup timer is installed and firing, and the restore has been verified against
  a real production dump — but the backups land in a MinIO container on the *same VPS* as the
  database, since this project has no AWS account. That is not off-host durability; pointing
  it at real S3 is one line of config and no code change.
- A deploy is health-gated, not zero-downtime: a bad image never stays deployed, but the drill
  measured 74 seconds of errors on the public domain while the gate caught one and rolled
  back. Blue/green would close that and hasn't been built.
- After a Redis restart the connection pool keeps handing out closed connections, so a handful
  of requests get a `500` before it drains — found by the chaos suite, documented in
  `docs/failure-modes.md`, not yet fixed.
- The Phase 6 load-test matrix (worker count x destinations x health profile) has reusable
  tooling (`tests/load/`) but has not yet been run end-to-end and published in
  `docs/load-test-results.md`.
- No Grafana dashboard and no Sentry — explicitly nice-to-have per the project plan, not
  oversights.
