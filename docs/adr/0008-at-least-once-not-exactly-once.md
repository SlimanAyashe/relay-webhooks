# 0008. At-least-once delivery, not attempted exactly-once

## Decision

Relay guarantees at-least-once delivery to a destination and makes no attempt to
additionally suppress the resulting duplicates on its own side (no dedup ledger, no
ack-before-send, no distributed transaction spanning the outbound HTTP call). The only
mechanism offered toward exactly-once-*like* behavior is a stable `X-Relay-Delivery-Id`
header on every attempt of a given delivery, so a receiver that wants to de-duplicate can
do so cheaply on its own side. This is a deliberate absence, not an oversight still to be
built.

## Context

Phase 2 (`docs/adr/0004-phase-2-delivery-engine.md`) already established the mechanics
that make duplicates possible: a dispatcher can send the HTTP request and then die before
acking the stream message (`XREADGROUP`), and the reaper's `XAUTOCLAIM` will correctly
redeliver that message to a new worker, which sends the request again. That ADR frames
this as a consequence of choosing Redis Streams' at-least-once semantics over a
hypothetical exactly-once broker. This ADR exists because "at-least-once" isn't just a
property that falls out of the queue choice -- it's an explicit, separate design decision
about what Relay promises its callers, one the project plan calls out by name as needing
its own comprehension check, and conflating it with the queue-mechanism ADR would bury
the "why not attempt exactly-once anyway" reasoning inside a different decision's alternatives
list. `docs/guarantees.md` states the promise ("Delivery is at-least-once, never
exactly-once... this is by design, not a bug") and `docs/failure-modes.md` pins it down
with a real test
(`tests/integration/test_dispatcher_worker.py::test_redelivery_after_a_post_send_pre_ack_crash_produces_an_observable_duplicate`);
this ADR is where the alternatives that were *not* built get written down.

Two different "exactly-once" questions are worth separating, because Relay answers them
differently:
- **Ingestion** (`POST /v1/events`): *is* effectively exactly-once, via the
  `Idempotency-Key` + `UNIQUE(tenant_id, idempotency_key)` constraint
  (`docs/adr/0003-phase-1-api-domain.md`). A retried client request never creates a second
  event.
- **Delivery** (Relay calling a tenant's HTTPS endpoint): is at-least-once only. This ADR
  is about that second question.

## Alternatives considered

- **A distributed transaction (or two-phase commit) spanning the outbound HTTP call and
  the stream ack**: would need the destination -- an arbitrary third-party HTTPS
  endpoint Relay does not control or trust -- to participate in a transaction protocol.
  No webhook receiver on the internet implements 2PC with its caller; this isn't a
  feasibility tradeoff so much as a structural impossibility for the problem Relay is
  solving. Ruled out immediately rather than partially attempted.
- **Ack-before-send** (mark the stream message acked, *then* attempt the HTTP call)
  instead of ack-after-attempt: would close the duplicate-delivery window, since a crash
  after ack can no longer trigger a redelivery of that same message. It opens the
  opposite and strictly worse hole instead: a crash between "acked" and "actually sent"
  now *loses* the delivery outright, with nothing left in the stream's pending-entries
  list to reclaim it. Relay's explicit guarantee is that an accepted event is never
  silently dropped; trading a duplicate (survivable by the receiver, per below) for a
  silent loss (unrecoverable) is the wrong side of that tradeoff.
- **A Relay-side deduplication ledger** (record every `delivery_id` + attempt outcome
  Relay has definitely-successfully sent, consult it before resending on reclaim): sounds
  like it would give exactly-once, but it doesn't -- the crash window this project's
  seventh bulletproof test targets is specifically *after the HTTP request is sent* but
  *before* any local state (a ledger row included) is durably written. The same crash
  that would skip acking the stream message would just as easily skip writing the ledger
  row. A ledger only moves the unclosable race from "stream ack" to "ledger write"; it
  adds a second write path to reason about without removing the fundamental problem, which
  is that Relay cannot distinguish "the destination received the request and is about to
  respond" from "the destination never received it" once the connection is interrupted
  mid-flight (`docs/guarantees.md`'s "Not guaranteed" section states this directly).
- **Doing nothing to help receiver-side dedup** (at-least-once, full stop, no header
  correlating repeated attempts of the same logical delivery): the simplest option, but it
  leaves a receiver that *does* want to de-duplicate with no cheap way to do it -- it would
  have to hash the body or invent its own correlation scheme. Rejected in favor of the
  header below, which costs nothing on Relay's side and meaningfully helps the receiver
  that wants it.
- **What was actually built**: every attempt of a given delivery carries the same
  `X-Relay-Delivery-Id` (the delivery's own id, stable across every retry and every
  redelivery of that delivery) alongside the HMAC signature
  (`relay.services.deliveries.service`, `docs/guarantees.md`'s replay-window note). A
  receiver that wants stronger-than-at-least-once behavior can track delivery ids it has
  already processed and de-duplicate on its own terms -- Relay hands it the one piece of
  information (a stable, signed, unforgeable correlation id) that makes that cheap,
  without pretending to solve the problem itself.

## Why

Every alternative above that looked like it could get closer to exactly-once either
requires cooperation Relay cannot compel from an arbitrary third-party HTTP endpoint (2PC),
or moves the unclosable crash window somewhere else without closing it (a dedup ledger),
or trades a survivable failure mode for an unsurvivable one (ack-before-send). None of them
change the one fact that makes exactly-once impossible here: once Relay's process has
handed a request to the network, it cannot always tell afterward whether the destination
received it, and there is no way to make "send the request" and "durably record that it
was sent" atomic with respect to an external HTTP call. Naming that clearly and building
the one thing that actually helps (a stable delivery id for receiver-side dedup) is more
honest than a mechanism whose name promises more than it delivers.

## Tradeoff accepted

Every receiver behind Relay must be idempotent to be correct, full stop -- this is pushed
onto every tenant's integration code, not solved centrally. A tenant who doesn't design
for it will, eventually, double-process some side effect (charge a customer twice, send a
duplicate notification). Accepted because the alternative -- Relay silently dropping
events to avoid ever duplicating one -- is a strictly worse guarantee for a webhook
delivery service to make, and because the cost is bounded and documented up front rather
than discovered in production: `docs/guarantees.md` states it as a top-level "Not
guaranteed" item, `X-Relay-Delivery-Id` is provided specifically to make handling it cheap,
and test #4 in the project's twelve-scenario definition of done exists to make this
concrete and demonstrable rather than a footnote.
