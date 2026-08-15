# 0004. Phase 2 delivery engine decisions

## Decision

Four decisions define Phase 2's shape: a transactional outbox (the event and its outbox row
commit together; the relay worker polls and publishes separately) rather than publishing to
Redis directly from the request path; Redis Streams consumer groups (not Celery or RabbitMQ) as
the work queue between the relay and the dispatcher; full-jitter exponential backoff
(`min(cap, base * 2^attempt)`, then a uniform draw from `[0, bound)`) for retry scheduling; and a
reaper that reclaims stale stream entries via `XAUTOCLAIM` and reprocesses them under their
original message ID rather than republishing them as new messages.

## Context

Phase 2 turns the durably-committed events from Phase 1 into actual outbound deliveries. The
seven "bulletproof" guarantees in the project plan depend directly on this phase: at-least-once
delivery, crash recovery, and jittered retry/backoff are what Phase 2 exists to build. Every
choice here needs an interview-defensible answer, since "why does this survive a crash" is the
question the whole project is built to answer well.

## Alternatives considered

- **Publishing straight to Redis from the event-ingest request**, skipping the outbox: fewer
  moving parts (no relay worker, no outbox table), but a crash between "the response is sent"
  and "the Redis publish lands" loses the event outright -- Redis isn't part of the Postgres
  transaction, so there's no way to make that atomic without one. Writing the event and its
  outbox row in a single Postgres transaction means the durability guarantee ("a `202` is never a
  lie") holds regardless of what happens next; the relay's only job is to notice pending rows and
  publish them, and if it dies before or during that, the row is simply still there to notice
  again on the next run.
- **Celery or RabbitMQ** instead of Redis Streams for the relay-to-dispatcher handoff: both are
  mature, battle-tested choices, and Celery in particular is the default most teams reach for.
  Redis Streams was chosen because the project already depends on Redis for the retry-schedule
  ZSET, so Streams doesn't add a new piece of infrastructure to run and reason about -- and
  because building the consumer-group claim/ack/`XAUTOCLAIM` flow by hand is a stronger signal of
  understanding at-least-once delivery than configuring a framework that already hides those
  mechanics. The honest cost: Celery's broker-agnostic task routing, retries-as-a-decorator, and
  Flower-style monitoring are real conveniences this project now reimplements piece by piece.
- **Fixed or capped-only backoff** (no jitter, or jitter only up to a fixed floor) instead of full
  jitter: simpler to reason about and to predict exact retry timings, but every failed delivery
  to the same flaky endpoint would then retry at (nearly) the same instant -- and if that endpoint
  recovers, all of them land on it simultaneously, the exact retry-storm failure mode full jitter
  exists to prevent. `min(cap, base * 2^attempt)` sets the ceiling; drawing uniformly from
  `[0, ceiling)` -- rather than, say, `[ceiling/2, ceiling]` -- is what actually spreads retries
  out instead of just capping how late they can land.
- **Re-`XADD`ing a reclaimed message as a brand-new stream entry** instead of reprocessing it
  under its original message ID: simpler to implement (no need to share processing logic between
  the dispatcher and the reaper), but it discards the point of `XREADGROUP`'s pending-entries
  list -- a reclaimed message re-added as new loses its claim history and becomes
  indistinguishable from a fresh delivery, muddying what "at-least-once, not exactly-once" looks
  like in the stream's own bookkeeping. Reprocessing under the same ID and acking it once done
  keeps the PEL as the single source of truth for "what's still outstanding."

## Tradeoff accepted

The outbox means every event pays for two writes (the event row, the outbox row) and a second
read-and-publish pass by the relay, instead of one write and an immediate publish -- accepted
because the alternative is a real, unrecoverable event-loss window, and Phase 2's entire reason
for existing is closing that window. One residual asymmetry worth naming: the relay's fan-out
transaction does the Redis `XADD` *before* the Postgres commit that marks the outbox row
processed, since Redis isn't part of that transaction. A crash between those two steps leaves the
outbox row `pending` (rolled back), so the next relay run re-fans-out the same event and
republishes it -- an extra duplicate delivery is possible, never a lost one, which is consistent
with the at-least-once guarantee this project claims rather than a gap in it.

Redis Streams over a mature broker means this project owns its own crash-recovery mechanics
(`SKIP LOCKED` claiming, `XREADGROUP`/`XACK`, `XAUTOCLAIM`) instead of getting them for free --
accepted deliberately, since demonstrating that reasoning is the point of the project, not a cost
to minimize.

Full jitter means retry timing is genuinely unpredictable within its bound -- a specific
delivery's exact next-attempt time can't be forecast, only bounded -- which is a real
debuggability cost accepted in exchange for not amplifying an outage the moment a downstream
recovers. `docs/failure-modes.md`'s per-attempt evidence is what substitutes for predictability
when narrating this to an interviewer.
