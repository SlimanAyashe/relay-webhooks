# Live verification

How this project answers "how do you know it works in production?" -- and why "the unit
tests pass" is the answer that question exists to disqualify.

Phases 0-7 built the guarantees and tested them in CI against testcontainers. Everything CI
structurally cannot see is verified here instead: the reverse proxy, the production
environment variables, the real workers dying and coming back, the backup timer, the egress
posture of the host, and the gap between "the suite passes against disposable containers"
and "the thing on the domain does what the README claims."

The design decisions behind all of it -- testing production rather than a staging clone,
killing containers rather than injecting faults, pinning the suite to `docs/guarantees.md`
rather than sweeping broadly, and proving operations by executed drills rather than by the
existence of scripts -- are in `docs/adr/0009-phase-8-live-verification.md`.

## The three things that run

| What | Where | When | Needs |
| --- | --- | --- | --- |
| Guarantee-pinned smoke suite | `tests/e2e/` | after every deploy, and on demand | HTTP access to a deployment |
| Chaos suite | `tests/chaos/` | on demand, before releases | Docker access to the stack's containers |
| Operational drills | `scripts/`, recorded in `docs/runbook.md` | on demand, periodically | shell on the host |

Neither live suite is in `pyproject.toml`'s `testpaths`, so a bare `pytest` never picks them
up: they are opt-in by path. Nothing under `tests/e2e/` or `tests/chaos/` imports `relay.*`
either -- they talk to the deployment over HTTP and to its containers over `docker`, exactly
as any other client or operator would. An import of the application's own code would quietly
turn a live test back into an in-process one.

## Running the smoke suite

```bash
# against production
RELAY_E2E_BASE_URL=https://relay.bookr.tech \
RELAY_E2E_RECEIVER_BASE_URL=https://relay.bookr.tech \
  uv run pytest tests/e2e -v

# against local compose (deliveries can't be proven -- see below)
uv run pytest tests/e2e -v
```

| Variable | Default | What it does |
| --- | --- | --- |
| `RELAY_E2E_BASE_URL` | `http://localhost:8000` | where the API is called |
| `RELAY_E2E_RECEIVER_BASE_URL` | same as base url | the origin the built-in `/mock/*` receivers are registered under |

**Why there are two URLs.** A registered endpoint must be `https`, and Relay's own SSRF
guard refuses any destination that resolves to loopback/RFC1918 or targets a port outside
80/443. That is the guarantee, working as intended -- so a suite pointed at
`http://localhost:8000` genuinely cannot prove a delivery. Rather than weaken the guard for
tests, the delivery-dependent tests skip with a message saying exactly that, and the receiver
origin is separately configurable so a local run can still drive deliveries through the
public origin. `tests/e2e/config.py` holds that logic.

The deploy workflow runs this suite after the `/readyz` gate: a deploy is "done" when the
guarantees pass against the new container, not when it reports ready. From a GitHub runner
the few checks that read container stdout or the dispatcher's own metrics port skip
automatically (no Docker access to the stack); run the suite on the host to cover those.

Budget roughly **6-8 minutes**: `POST /v1/sandbox` is rate-limited to about one per 20
seconds per IP, and the DLQ test (marked `slow`) waits out a real retry budget and a real
breaker cooldown. Both are controls under test elsewhere in the same suite, so the suite
obeys them rather than working around them.

## Running the chaos suite

```bash
RELAY_E2E_BASE_URL=https://relay.bookr.tech \
RELAY_E2E_RECEIVER_BASE_URL=https://relay.bookr.tech \
  uv run pytest tests/chaos -v
```

These stop, `kill -9` and restart real containers of a real stack, so they need to run on
the host that runs it. An autouse fixture brings every container back up after each test,
pass or fail. Expect **8-12 minutes**: the reaper's reclaim threshold alone is 30 seconds of
idle time, and no amount of cleverness makes a crash-recovery test fast.

| Test | Proves |
| --- | --- |
| `tests/chaos/test_relay_down_across_ingest.py` | a `202` is not a lie even with the relay process gone: the outbox row sits `pending`, nothing is on the stream, and the delivery completes when the relay returns |
| `tests/chaos/test_dispatcher_killed_mid_delivery.py` | `kill -9` mid-delivery leaves the message in the pending-entries list; the reaper reclaims it under its **original** stream id, and the receiver sees the duplicate that at-least-once promises |
| `tests/chaos/test_datastore_restarts_under_traffic.py` | restarting Postgres and then Redis under a live trickle loses no event that was accepted -- failed requests are fine, vanished ones are not |

## What the live suite pins

One test per promise in `docs/guarantees.md`. The mapping is deliberately one-to-one so the
suite's size is justifiable line by line, and so a live failure is by construction a broken
promise rather than a flaky test.

| Guarantee | Live test |
| --- | --- |
| A `202` is durably persisted before the response returns | `test_accepted_event_returns_202_and_a_location_for_the_created_event`, `test_an_accepted_event_is_actually_delivered_to_its_endpoint` |
| Delivery is at-least-once, never exactly-once | `tests/chaos/test_dispatcher_killed_mid_delivery.py` (the duplicate, at the receiver) |
| Idempotency keys prevent duplicate logical ingestion | `test_same_idempotency_key_with_an_identical_body_returns_the_original_event`, `test_same_idempotency_key_with_a_differing_body_is_a_409_problem_document` |
| Retries use bounded exponential backoff with full jitter | `test_a_500_schedules_a_retry_inside_the_full_jitter_bound_for_that_attempt` |
| Exhausted retries land in the DLQ and stay replayable | `test_exhausted_retries_land_in_the_dlq_and_replay_starts_a_fresh_chain` |
| Every outbound delivery is HMAC-signed with a timestamp | `test_a_real_delivery_carries_a_signature_that_verifies_against_the_endpoint_secret`, `test_an_out_of_repo_verifier_accepts_the_live_signature_and_rejects_a_tampered_byte` |
| Destinations are SSRF-restricted, not merely "checked" | `tests/e2e/test_ssrf_live.py` (five probes: loopback, RFC1918, metadata, a public hostname resolving to a private address, and a redirect hop) |
| Sandbox tenants are hard-capped on every axis independently | `tests/e2e/test_abuse_controls_live.py` |
| The console's mock receivers weaken no guarantee | `test_the_redirect_probe_reached_the_mock_but_never_the_metadata_address` |
| Every log line, from every process, is structured JSON | `test_every_process_logs_structured_json_to_stdout` |
| One correlation id spans ingest and worker log lines | `test_the_same_correlation_id_appears_on_api_and_worker_log_lines` |
| `/healthz` and `/readyz` are genuinely distinct | `test_healthz_and_readyz_report_different_things` |
| Breaker state and outcomes are observable via Prometheus | `test_the_api_exposes_its_request_latency_histogram`, `test_the_dispatcher_exposes_attempt_outcomes_and_breaker_state` |
| Auth rejects revoked/expired/unknown keys; tenants are isolated | `tests/e2e/test_auth_isolation_live.py` |
| The nightly backup exists and has actually been restored | the drill in `docs/runbook.md`, dated in `docs/PROJECT_STATUS.md` |

`docs/failure-scenarios.md` maps the plan's twelve failure scenarios to the same suites, and
`tests/unit/test_failure_scenario_audit.py` fails the build if any named test disappears.

## What it deliberately does not cover

- **A 403 for a key lacking a scope.** A sandbox key carries every scope the public API
  requires, and there is no public route that issues a narrower one, so the live suite cannot
  produce this case. Covered in `tests/integration/test_auth.py`.
- **DNS rebinding inside a single request.** Proving it needs a resolver that answers
  differently on a second lookup, which is a unit-level fixture, not something to arrange
  against production DNS. `tests/integration/test_http_sender_ssrf.py` proves the pinned
  transport directly; the live suite instead probes the shape an attacker can actually set
  up -- a public hostname whose record points at a private address.
- **Breadth.** CI owns breadth; a regression outside the pinned promises can reach production
  without tripping a live test. That is the accepted cost of keeping this suite small enough
  to run on every deploy and small enough that every test is worth narrating.

## Test data in production

The smoke suite writes into the real database and the real metrics: sandbox tenants, a
handful of events, some deliberately failing deliveries. The blast radius is bounded by
sandbox tenancy (scoped keys, hard quotas, a 60-minute TTL), and it is visible: p95 charts
show a smoke-test blip after every deploy. Documented rather than hidden -- see the ADR's
"tradeoff accepted".
