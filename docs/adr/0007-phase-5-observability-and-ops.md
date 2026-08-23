# 0007. Phase 5 observability and ops decisions

## Decision

Five decisions define Phase 5's shape: `correlation_id` is not a new identifier -- it
*is* the existing `trace_id`/`X-Trace-Id` value (`relay.api.middleware.TraceIdMiddleware`),
now also bound into structlog's contextvars and carried onto the `Event` row so
`relay.workers.relay`/`dispatcher`/`scheduler`/`reaper` can bind the same value into every
worker log line for that event's deliveries -- including retries, via a small JSON envelope
in the retry-ZSET member, not just the first attempt; stdlib `logging` is bridged into JSON
through `structlog.stdlib.ProcessorFormatter` rather than every existing `logging.getLogger`
call site being rewritten to structlog's native API; Prometheus metrics are recorded in
whichever process actually observes them and served from **two** scrape targets, not one --
the `api` process's `GET /metrics` (request latency) and the dispatcher's own
`prometheus_client.start_http_server()` exporter (attempts by outcome, breaker state, queue
depth/in-flight); the nightly backup and its restore drill are written against a generic
S3-compatible endpoint (`boto3` + configurable `endpoint_url`) so the exact same code path
runs against real AWS S3 in production and against a local MinIO container for the
actually-executed drill this phase requires; and log rotation is Docker's own `json-file`
driver options set in the compose files, not host-level `logrotate`.

## Context

Phases 0-4 built a system with real guarantees (at-least-once delivery, signing, SSRF
defense, a breaker, a DLQ) and, since Phase 4, a public demo console exercising all of it --
but almost nothing about *how it's actually running* was visible without reading application
code or querying Postgres directly. Phase 5's job, per the project plan, is narrower than
"add observability" in the abstract: structlog JSON, correlation IDs that actually span
process boundaries through Redis message headers ("this part is worth doing -- it's
genuinely useful and rarely seen," per the plan), a Prometheus `/metrics`, and a nightly
backup with a restore test that is *executed*, not merely scripted. Grafana and Sentry are
explicitly named as skippable. Phase 5 is optional (week 9, weeks 9-12 are all droppable per
the plan's own scope discipline) -- everything here is additive polish, nothing it touches
was required for Phase 4's console to work.

## Alternatives considered

- **Minting a second, independent `correlation_id`** generated fresh wherever it was needed,
  instead of reusing `trace_id`: this is what a naive reading of "add correlation IDs" would
  produce, and it would mean two different ids identifying the same request/event depending
  on which one happened to get logged -- confusing to debug and a second piece of state to
  keep synchronized for zero benefit. `trace_id` already had the right lifecycle (generated
  or honored at the API edge, echoed on the response, present in every error body) for
  everything the plan asks of a correlation id; the only genuinely new work is *propagating*
  it somewhere it didn't reach before (structlog's context, the `Event` row, the Redis stream
  message, the retry ZSET), not minting a new value. Reuse over duplication is the same
  judgment call `docs/adr/0006-phase-4-demo-console.md` made for the sandbox mechanism.
- **Only propagating the correlation id on the first delivery attempt**, not through retries:
  the simpler implementation -- `relay.workers.relay` embeds it in the first `XADD`, done.
  Rejected because most deliveries that fail at all take more than one attempt, and a
  correlation id that silently disappears after the first retry would make the feature
  useless for exactly the failures worth debugging (a flaky destination retried five times).
  `relay.infra.retry_schedule.schedule_retry` now stores a small JSON envelope
  (`{"delivery_id": ..., "correlation_id": ...}`) as the ZSET member instead of a bare UUID
  string, and `relay.workers.scheduler` re-`XADD`s it on every fire -- proven by
  `tests/integration/test_scheduler_worker.py::test_run_once_fires_only_due_retries`'s
  correlation-id assertion, not just the happy first-attempt path.
- **`prometheus_client`'s multiprocess mode** (`PROMETHEUS_MULTIPROCESS_DIR`, a shared
  volume, `multiprocess.MultiProcessCollector`) to get every process's metrics onto one
  `/metrics` endpoint: this is the standard answer for *N identical workers of the same app*
  (its original use case is a preforking WSGI server), not five distinct long-running
  processes with different lifecycles and different metrics to report. It also has a real,
  documented operational sharp edge -- a process that dies without calling
  `multiprocess.mark_process_dead(pid)` leaves stale per-PID metric files behind until
  something cleans them up, which nothing in this Docker Compose deployment would do
  automatically. Two scrape targets (api, dispatcher) is less machinery and has no
  stale-state failure mode: a dead dispatcher's `/metrics` just becomes unscrapeable, which
  is itself a normal, alertable Prometheus signal ("target down"), not a silently-stale value.
- **Recomputing queue depth / in-flight live from Redis inside the api process's `/metrics`
  handler** (an `XLEN`/`XPENDING` call at scrape time) instead of having the dispatcher
  sample and serve them: genuinely simpler for those two specific gauges, since they're pure
  Redis state with no need to be "observed" by any particular process. Rejected in favor of
  consistency: `delivery_attempts_total` and `circuit_breaker_state` are true point-in-time
  *events* the dispatcher observes (an attempt outcome, a breaker transition) that cannot be
  recomputed from a live query the same way, so they have to be dispatcher-served regardless.
  Splitting queue depth/in-flight off to a *third* place they're computed (live, in the api
  process) would mean three different metrics-sourcing patterns instead of two consistent
  ones ("api serves what it observes; dispatcher serves what it observes") for a marginal
  simplification of two gauges. The dispatcher already reads `XLEN`/`XPENDING` for free on
  every poll iteration it would otherwise run anyway.
- **A blanket rewrite of every existing `logging.getLogger(__name__)` call site** to
  structlog's native keyword-argument API: would make every log line's *shape* more uniform,
  but touches roughly a dozen files for a phase whose actual requirement is "every log line
  is machine-parseable JSON," which the `structlog.stdlib.ProcessorFormatter` bridge achieves
  for existing call sites (and third-party loggers like uvicorn's and sqlalchemy's) with zero
  changes to them. New Phase 5 code (the middleware's request-completed line, the
  dispatcher's per-attempt line) uses structlog's native API directly, since it's new code
  with no existing call site to preserve.
- **Host-level `logrotate`** targeting Docker's JSON log files on the VPS, matching how the
  egress-firewall rules in `docs/runbook.md` are documented-but-not-applied because they'd
  touch shared host state: rejected here specifically because Docker's own `json-file`
  driver options (`max-size`, `max-file`) solve the identical problem without touching
  anything outside this repo's own compose files -- no host cron job, no coordination with
  whoever owns the VPS's other services needed. Applied directly, not just documented.

## Why

Every metric and log line answers a question someone holding the pager would actually ask:
*is this specific event's delivery visible end-to-end, and can I find every line about it*
(correlation id); *is a destination's breaker actually behaving the way the code claims*
(the gauge, with its own direct test rather than trusting the domain-level test alone);
*is the queue backing up, and by how much, right now* (the dispatcher's live-sampled
gauges). None of these needed Grafana or a second infrastructure component to be true and
checkable -- `curl :8000/metrics` / `curl :9100/metrics` and a `docker logs | jq` is the
whole verification story, matching the plan's explicit steer away from Sentry/Grafana this
phase and its general distaste for machinery that isn't earning its complexity.

## Tradeoff accepted

Two scrape targets instead of one is a real, if small, operational cost: whoever eventually
points a real Prometheus server at this deployment needs two `scrape_configs` entries, not
one, and a dashboard correlating "request latency spiked" with "breaker just opened" is
querying two different targets rather than one unified series set. Accepted because the
alternative (multiprocess mode) trades a config-file line for a class of stale-metric bugs
that's genuinely harder to reason about and explain than "there are two exporters."

`GET /metrics` is deliberately unauthenticated (matching how every Prometheus target works --
a scraper has no tenant API key to send), which means if it is ever routed through the
public Traefik entrypoint in production, operational detail (error-class breakdowns,
per-endpoint breaker state, request volume) becomes publicly readable. This phase does not
add a Traefik label routing `/metrics` publicly, and does not add one restricting it either
-- `docs/PROJECT_STATUS.md` already establishes that touching the shared VPS's Traefik
config is out of bounds for this repo's tooling to do unilaterally. Named here as a known
gap: before any real Prometheus server is pointed at the production deployment, whoever has
authority over that Traefik instance needs to add an IP-allowlist (or simply never add a
public router for it), not assume `/metrics` is safe to leave reachable at
`api.relay.bookr.tech/metrics` by default.

The restore drill that Phase 5 requires be *executed*, not merely scripted, was run against
MinIO standing in for S3 (`docs/PROJECT_STATUS.md` has the dated record, including the row
counts that matched between the live database and the restored scratch container) rather
than a real AWS account, because no AWS credentials exist anywhere in this project. The
scripts (`scripts/backup_postgres.py`, `scripts/restore_drill.py`) take the S3 endpoint as a
setting specifically so this is a deployment-time configuration difference, not a
code-path difference -- production pointing at real S3 needs `BACKUP_S3_ENDPOINT_URL`
left unset and real AWS credentials in the environment, nothing else.
