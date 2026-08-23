# Load testing

The plan's Phase 6 performance section calls for sweeping three axes and recording
throughput, p50/p95/p99, error rate, and queue depth for each cell:

| Axis | Values |
| --- | --- |
| Worker count | 1, 4, 8 |
| Concurrent destinations | 10, 100 |
| Destination health | all-healthy, 50%-failing |

This directory holds the reusable harness for that sweep. **Running the full matrix and
publishing results in `docs/load-test-results.md` is separate, later work** (tracked as
its own backlog items, partly gated on Phase 5's Prometheus queue-depth gauge) -- what's
here is the infrastructure: a k6 script, a fixture-provisioning script, and an
orchestrator, all individually runnable today.

## Files

- **`events_ingest.js`** -- the k6 script. Authenticates as one tenant via a static
  `X-API-Key` header (Relay has no login flow) and posts to `POST /v1/events` at a
  controlled arrival rate, each request carrying a unique `Idempotency-Key` so every
  request is a genuinely new logical event. Configured entirely via `-e KEY=value` env
  vars (`RELAY_BASE_URL`, `RELAY_API_KEY`, `RELAY_EVENT_TYPE`, `RELAY_RATE`,
  `RELAY_DURATION`, ...) -- see the file's header comment for the full list and defaults.
  Throughput and latency come from k6's own summary output (`http_reqs`,
  `http_req_duration`); pass `--summary-export=path.json` for a machine-readable copy.
- **`../../scripts/load_test_setup.py`** -- provisions a full (non-sandbox) tenant, an
  API key, and N endpoints pointed at one of the Phase 4 mock receivers, before a run.
  `--count` is the concurrent-destinations axis (10 or 100); `--profile healthy` targets
  `/mock/always-200`, `--profile failing` targets `/mock/flaky-50` (the plan's
  50%-failing cell -- the one it flags as mattering most, since that's where the retry
  scheduler and circuit breaker interact and queue depth behaves non-linearly).
- **`run_matrix.sh`** -- orchestrates the above across the full matrix (or a single
  cell): scales `relay-worker` via `docker compose up --scale`, provisions a fresh
  tenant/endpoint fixture for that cell, runs k6, and writes both to `results/` (gitignored
  scratch output, not committed).

## Why a script provisions the tenant instead of the sandbox

The only HTTP-reachable way to get a tenant identity is `POST /v1/sandbox`, but its
quotas (`sandbox_max_endpoints=3`, `sandbox_max_events=20`,
`sandbox_rate_limit_requests_per_second=1.0`, a 60-minute key TTL) exist specifically to
make it useless for sustained load -- see `docs/adr/0006-phase-4-demo-console.md`.
`scripts/load_test_setup.py` instead creates a normal tenant directly against the
database, through the same service layer the API uses, so it gets the same
unlimited-by-quota shape a real tenant has.

## The HTTPS constraint

`EndpointService` rejects any endpoint URL that isn't `https://`
(`relay.services.endpoints.service.EndpointService._validate_url`) -- a production
security invariant (see `docs/adr/0005-phase-3-security-resilience.md`), not something
this harness works around. The local Compose stack's Caddy (`docker/compose.yml`,
`docker/Caddyfile`) only terminates plain HTTP on `:8080`, so registering a mock receiver
as a destination for a purely local run isn't possible without standing up TLS yourself.

In practice this means `RELAY_BASE_URL` / `--base-url` should point at an
HTTPS-reachable deployment whose `/mock/*` router the worker containers can actually
reach to deliver to -- the deployed instance (`https://api.relay.bookr.tech`) is the
straightforward choice, since it already has real TLS via the VPS's shared Traefik (see
`docs/adr/0002-shared-vps-traefik.md`). `run_matrix.sh` still scales *local* Compose
workers (`relay-worker`), so if you want the worker-count axis to mean something for a
non-local `RELAY_BASE_URL`, run the matching scaling command against
`docker/compose.prod.yml` on the VPS itself instead, or adapt `run_matrix.sh`'s
`scale_workers` function -- it's one `docker compose up --scale` call.

## Quick start (single cell)

```bash
export RELAY_BASE_URL=https://api.relay.bookr.tech

uv run python scripts/load_test_setup.py \
  --base-url "$RELAY_BASE_URL" --count 10 --profile healthy \
  --output tests/load/results/fixture.json

API_KEY=$(python3 -c "import json; print(json.load(open('tests/load/results/fixture.json'))['api_key'])")

k6 run \
  -e RELAY_BASE_URL="$RELAY_BASE_URL" -e RELAY_API_KEY="$API_KEY" \
  --summary-export=tests/load/results/summary.json \
  tests/load/events_ingest.js
```

## Full matrix

```bash
RELAY_BASE_URL=https://api.relay.bookr.tech tests/load/run_matrix.sh
```

Override any axis, or run one cell, per the header comment in `run_matrix.sh`:

```bash
WORKER_COUNTS="4" DEST_COUNTS="100" PROFILES="failing" \
  RELAY_BASE_URL=https://api.relay.bookr.tech tests/load/run_matrix.sh

# equivalently, single-cell flags:
RELAY_BASE_URL=https://api.relay.bookr.tech \
  tests/load/run_matrix.sh --workers 4 --destinations 100 --profile failing
```

## Worker count without hand-editing

No Compose override file is needed: `docker compose`'s own `--scale` flag changes
replica count for any service without a fixed `container_name` (none of `docker/compose.yml`'s
services set one), so `run_matrix.sh` does exactly:

```bash
docker compose -f docker/compose.yml up -d --scale relay-worker=4 \
  postgres redis api relay-worker dispatcher scheduler reaper
```

`dispatcher`, `scheduler`, and `reaper` stay at 1 replica each in this harness (only
`relay-worker`, the outbox-to-stream fan-out process, is on the plan's "worker count"
axis) -- pass additional `--scale` flags if the matrix should scale those too.
