# Interview prep

Phase 7 scope, per [RELAY-PLAN.md](../RELAY-PLAN.md): rehearse "the seven things to make
bulletproof" out loud against [docs/failure-modes.md](failure-modes.md) until each one holds up
to a follow-up question without notes. Each section below is a fixed script — one spoken
narrative, short enough to say in under a minute, anchored to the ADR that recorded the decision
and the test(s) that prove the behavior actually happens.

The seven topics, per the plan: transactional outbox → at-least-once semantics → idempotency →
retry/backoff → crash recovery → SSRF defense → HMAC signing. Outbox and at-least-once share one
talk track below since the second is a direct consequence of the first.

## 1–2. Transactional outbox and at-least-once delivery

**Decision record:** [docs/adr/0004-phase-2-delivery-engine.md](adr/0004-phase-2-delivery-engine.md)
**Proof:** failure-modes rows 9–10 (`docs/failure-modes.md`) — outbox recovery and `SKIP LOCKED`
claiming — plus row 13 (duplicate after post-send crash), and plan scenarios #1, #2, #4.
**Tests:** `test_outbox_row_committed_before_a_relay_run_is_recovered_on_the_next_run`,
`test_skip_locked_gives_a_claimed_row_to_exactly_one_concurrent_claimer`,
`test_redelivery_after_a_post_send_pre_ack_crash_produces_an_observable_duplicate`

**Talk track:**
A `202` from `POST /v1/events` is a promise, and the outbox pattern is what makes that promise
keepable. The event row and its outbox row commit in the same Postgres transaction — so if the
process dies the instant after that commit, before the relay has published anything, nothing is
lost: the outbox row is just sitting there `pending`, and the next relay run, whether that's a
restart or the same process's next tick, finds it and fans it out. There's no special recovery
path, because there was never anything to recover — only to notice. When multiple relay
instances run concurrently, `SELECT ... FOR UPDATE SKIP LOCKED` hands each due row to exactly
one claimer instead of blocking or double-processing. That claiming guarantee is what turns "the
row will eventually be picked up" into "at-least-once" specifically — not "exactly-once": if a
worker sends the HTTP request but dies before acking the stream message, the message gets
reclaimed and resent, and the receiver genuinely sees a duplicate. That's not a bug I fixed — a
timeout and a successful-send-then-crash are indistinguishable from the sender's side, so
at-least-once is the strongest honest claim, and it's why receivers are expected to be
idempotent on their end too.

## 3. Idempotency keys and duplicate ingest

**Decision record:** [docs/adr/0003-phase-1-api-domain.md](adr/0003-phase-1-api-domain.md)
**Proof:** failure-modes rows 1–3, plan scenarios #5, #6.
**Tests:** `test_duplicate_key_identical_body_returns_original_no_new_row`,
`test_duplicate_key_differing_body_raises_conflict_no_new_row`,
`test_duplicate_idempotency_key_raises_typed_conflict`

**Talk track:**
`Idempotency-Key` collapses a duplicate `POST /v1/events` into a single event row and a single
delivery per endpoint — the caller can retry a network timeout without fear of double-firing a
webhook. Same key, same body, replayed: the original event comes back unchanged, same id, same
`202`, no new row inserted. Same key, different body: that's a client bug, not a retry, so it's a
`409 Conflict` instead of silently accepting whichever body arrived first. The interesting case
is the race — two requests with the same `(tenant_id, key)` landing at the same instant. A
database-level `UNIQUE` constraint is the actual arbiter, not application logic: exactly one
`INSERT` wins, the loser's repository call raises a typed conflict inside a `SAVEPOINT` so the
surrounding transaction stays usable, and it resolves through the same identical-vs-differing
body logic as an ordinary duplicate. The lesson I'd draw out if pushed: idempotency under
concurrency has to be enforced by the database's own constraints, because anything checked in
application code first has a race window between the check and the insert.

## 4. Jittered exponential backoff

**Decision record:** [docs/adr/0004-phase-2-delivery-engine.md](adr/0004-phase-2-delivery-engine.md)
**Proof:** failure-modes rows 14–16, plan scenarios #7, #9.
**Tests:** `test_process_delivery_message_on_500_schedules_jittered_backoff`,
`test_run_once_fires_only_due_retries`

**Talk track:**
When a destination fails — a non-2xx status or a timeout — the delivery doesn't retry
immediately, it moves to `retrying` with `next_retry_at` set by `min(cap, base * 2^attempt)`,
plus full jitter. The exponential part is the obvious half: back off harder the more times
something has failed, and cap it so a chronically dead endpoint doesn't schedule a retry a week
out. The jitter is the half that actually matters and the half people skip: without it, every
delivery that failed during the same outage window comes back due at the same computed instant,
so the moment the destination recovers it gets hit with a synchronized retry storm — which can
knock it right back down. Jitter spreads that same set of retries across a window instead of a
point, so recovery looks like a ramp, not a spike. Mechanically, due retries live in a Redis
ZSET scored by `next_retry_at`; the scheduler's tick pops only what's actually due, atomically
via a Lua script, and re-`XADD`s each one onto the delivery stream exactly once.

## 5. Crash recovery and XAUTOCLAIM

**Decision record:** [docs/adr/0004-phase-2-delivery-engine.md](adr/0004-phase-2-delivery-engine.md)
**Proof:** failure-modes rows 12–13, plan scenarios #3, #4 — "the test to be able to narrate
without notes."
**Tests:** `test_run_once_reclaims_and_processes_a_message_a_dead_consumer_never_acked`,
`test_redelivery_after_a_post_send_pre_ack_crash_produces_an_observable_duplicate`

**Talk track:**
Redis Streams consumer groups track a pending-entries list per consumer — every message a
consumer has read via `XREADGROUP` but not yet acked. If a dispatcher dies mid-processing, that
message just sits in its PEL; it isn't lost, and it isn't reprocessed by anyone until something
decides to reclaim it. That's the reaper's job: it runs `XAUTOCLAIM` on a sweep, and once a
message has been pending past a configured idle threshold, ownership transfers to a live
consumer, which reprocesses it through the exact same delivery logic and acks the original
message id. The case I'd walk through carefully is a worker that dies *after* sending the HTTP
request but *before* acking — because from the stream's point of view that's indistinguishable
from a worker that died before sending anything, so the message gets reclaimed and resent either
way. If the destination actually received the first attempt, the receiver now sees two requests
for the same event. I don't try to prevent that; I proved it happens on purpose — `docker kill`
a worker mid-delivery, watch `XAUTOCLAIM` reclaim it, watch the duplicate land — because
preventing it would mean distributed exactly-once delivery, which isn't a thing you get by
trying harder.

## 6. SSRF defense and the DNS-rebinding residual risk

**Decision record:** [docs/adr/0005-phase-3-security-resilience.md](adr/0005-phase-3-security-resilience.md)
**Proof:** failure-modes rows 27–28, 38, plan scenario #11.
**Tests:** `test_redirect_to_forbidden_ip_is_blocked_with_no_connection_made`,
`test_dns_rebinding_connects_to_the_originally_validated_ip_not_a_fresh_lookup`,
`test_redirect_to_metadata_mock_is_blocked_end_to_end_with_no_connection_made`

**Talk track:**
I'd be careful not to say SSRF is "solved" — the honest claim is that destinations are
restricted, defended in layers, with one specific residual risk documented rather than hidden.
Every outbound hostname is resolved and the resulting address checked against forbidden ranges —
loopback, RFC1918, link-local, CGNAT, IPv6 ULA, and the cloud metadata address specifically —
before a connection is attempted. A naive version of that check has a TOCTOU hole: resolve,
validate, then let the HTTP client re-resolve when it actually connects, and a DNS record that
rebinds in between defeats the whole check. The transport here is IP-pinned — it connects to the
address the guard actually validated, never re-resolving mid-request — which closes that window
for a single request. Redirects get the same treatment: a `Location` header is revalidated by
the guard before the redirect hop is followed, which is what the console's `redirect-to-metadata`
mock proves end to end. What's not closed, and I'd say so before being asked: a hostname that's
genuinely public right now but gets reconfigured to point somewhere forbidden before the *next*
retry attempt is still caught, because the guard re-validates on every attempt — but a
compromised resolver, or a non-HTTP redirect mechanism the guard doesn't see, are outside this
guarantee's scope. That's why the runbook also documents network-layer egress rules as a second,
independent layer — the application check isn't meant to be the only thing standing between an
attacker and the internal network.

## 7. HMAC signing

**Decision record:** [docs/adr/0005-phase-3-security-resilience.md](adr/0005-phase-3-security-resilience.md)
**Proof:** failure-modes row 33.
**Tests:** `tests/unit/test_signing.py` (hypothesis-generated payloads and timestamps)

**Talk track:**
Every outbound delivery is signed by HMAC-SHA256 over `timestamp.body`, not just the body — the
timestamp is in the signed material, not a side channel, so it can't be stripped without
invalidating the signature. Verification happens with `hmac.compare_digest`, a constant-time
comparison, specifically so a byte-by-byte early-exit comparison can't leak information about
the correct signature through response timing. The timestamp also enforces a replay window —
`verify()` rejects a correctly-signed request whose timestamp is outside
`signature_tolerance_seconds` (300s by default) of "now" in either direction. That width is
chosen to absorb ordinary clock skew between Relay and a receiver, not to promise zero replay —
a captured request replayed inside that window still verifies, which is documented rather than
glossed over. A receiver that needs a stricter guarantee can additionally track
`X-Relay-Delivery-Id` values it's already seen within the tolerance window; that's a receiver-side
concern, not something the signing scheme itself claims to solve.

## Rehearsal log

Not yet run. Per the plan, this should be a timed, back-to-back, out-loud pass through all seven
talk tracks above, with this section updated afterward to note total time and anywhere a
follow-up question exposed a gap. Best done once Phase 5/6 have landed, so the surrounding
"what's deployed right now" claims in [docs/PROJECT_STATUS.md](PROJECT_STATUS.md) are current
when this gets rehearsed against them.

## Resume bullets (draft)

Per the plan's claims-discipline section — outcomes, not a tool inventory, and never the phrase
"production-grade":

- Built an at-least-once webhook delivery platform routing events through a transactional
  outbox and Redis Streams consumer groups, with idempotency keys, jittered retry scheduling,
  per-endpoint circuit breakers, and DLQ replay.
- Implemented SSRF-resistant outbound delivery with DNS-pinned connections and CIDR validation,
  HMAC-signed payloads, and sandbox quotas; verified crash recovery by killing workers
  mid-delivery and published k6 throughput/p95/p99 results.

The second bullet's k6 numbers are Phase 6 output — fill in once that lands.
