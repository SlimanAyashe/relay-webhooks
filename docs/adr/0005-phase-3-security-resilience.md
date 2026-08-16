# 0005. Phase 3 security and resilience decisions

## Decision

Three decisions define Phase 3's shape: closing the SSRF DNS-rebinding TOCTOU window by
connecting outbound requests to a pre-validated IP via a custom httpx transport, rather
than re-validating a hostname and handing that hostname back to the HTTP client; a
per-endpoint `asyncio.Semaphore` in the dispatcher instead of full per-tenant fair
scheduling; and a circuit breaker threshold of 5 consecutive failures with a 60-second
cooldown, persisted on the `endpoints` row itself rather than in a separate table or
in-memory.

## Context

Phase 2 shipped a delivery engine that would call whatever URL an endpoint has, with no
signing and no breaker -- fine while only a tenant who registered the endpoint could
trigger it, not safe once anything public-facing (the demo console, or just a malicious
tenant) can point Relay at an arbitrary URL, including internal ones. Phase 3 has to make
three separate kinds of promise true at once: the payload wasn't tampered with in transit
(signing), the destination isn't inside this deployment's own network (SSRF), and one dead
destination can't consume the resources every other tenant's deliveries need (breaker +
concurrency cap). Each has an interview-defensible "why this shape and not the simpler
one" answer, which is what this ADR is for.

## Alternatives considered

- **Re-resolving the hostname at connect time** (validate with `check_url()`, then just
  call `httpx.AsyncClient.post(original_url)`) instead of IP pinning: far simpler -- no
  custom transport, no `sni_hostname` extension juggling -- but it's exactly the TOCTOU bug
  SSRF guards get wrong. Between the guard's `getaddrinfo()` call returning a public IP and
  httpx's own internal resolution moments later, a short-TTL or attacker-controlled DNS
  record can rebind to a private address, and the connection goes there instead. Pinning
  means the address that was checked is the address that gets connected to, full stop --
  `relay.infra.pinned_transport.PinnedAsyncTransport` rewrites the request's URL host to
  the validated IP and sets `extensions["sni_hostname"]` so TLS SNI and certificate
  verification still target the real hostname, closing the window rather than narrowing it.
  Verified directly: `tests/integration/test_http_sender_ssrf.py::test_dns_rebinding_connects_to_the_originally_validated_ip_not_a_fresh_lookup`
  scripts a resolver that returns a public IP on the first call and a private one on the
  second, and asserts the request still lands on the first (validated) address, and that
  the resolver is only called once per `send()` call.
- **A denylist-configurable-via-settings SSRF guard**: letting `RFC1918`/loopback/etc. be
  tuned via env var looks flexible, but it's the one part of this project where flexibility
  is a liability -- a misconfigured or overly permissive deploy could silently disable the
  guard's actual purpose. The deny list lives as code constants in
  `relay.infra.ssrf_guard`; only the allowed *port* set is a setting
  (`ssrf_allowed_ports`), since port policy is a legitimate per-deployment choice in a way
  "should this service be allowed to reach 169.254.169.254" never is.
- **Full per-tenant fair scheduling** (a weighted queue per tenant, or per endpoint, with
  proper starvation-freedom guarantees) instead of a fixed-capacity semaphore per endpoint:
  the textbook-correct answer, and the one a systems-design interview might expect first.
  It's also a materially bigger piece of machinery -- tracking per-tenant queue depth,
  deciding a fairness policy, handling a tenant with many endpoints vs. one with few -- for
  a problem the breaker mostly already solves at the endpoint level (an endpoint failing
  enough to matter trips the breaker and stops consuming resources at all). A semaphore
  capped at `dispatcher_per_endpoint_concurrency` (default 3) means one slow-but-still-200
  destination (the case the breaker doesn't catch, since it isn't failing) can't hold 10 of
  the dispatcher's 10 global concurrency slots waiting on its 8-second response time. That's
  the specific gap being closed, not general fairness -- true per-tenant fairness stays in
  "known limitations," honestly, rather than half-implemented here.
- **A lower or higher breaker threshold/cooldown than 5 failures / 60 seconds**: no
  clean-room derivation exists for either number -- they're a judgment call, stated as one.
  5 consecutive failures is enough to rule out "one blip" (a single dropped packet, a
  transient 502) without waiting through a long losing streak while a dead endpoint keeps
  eating retry attempts; lower (e.g. 2-3) trips on noise a working destination will recover
  from on its own via ordinary backoff. 60 seconds as a cooldown is short enough that a
  demo (or a real recovering destination) doesn't sit dark for minutes, long enough that
  the breaker isn't just re-probing every few seconds and defeating its own purpose. Both
  are `Settings` fields (`breaker_failure_threshold`, `breaker_cooldown_seconds`) precisely
  because they're a tuning choice, not a security invariant like the SSRF deny list.
- **Recording a delivery_attempts row for a breaker-open skip**: would keep the attempt log
  as literally one row per `DeliveryAttemptService.attempt()` call, but it would also make a
  dead destination's attempt count -- and its position in the retry-budget-to-DLQ math --
  indistinguishable from a destination that's actually being hit repeatedly and failing.
  Chosen instead: a breaker-open deferral calls `DeliveryRepository.reschedule()` (no
  `delivery_attempts` row, no `attempt_count` increment, `next_retry_at` set to exactly
  when the cooldown elapses) rather than `mark_retrying()`. The tradeoff: the attempt log
  alone can no longer answer "how many times did Relay try to call this destination," since
  a delivery's true attempt count and the breaker's skip count are tracked separately (on
  the delivery and the endpoint respectively) -- accepted because conflating them would
  actively mislead whoever's reading the log to debug a stuck delivery.

## Why

Every one of these is the same shape of decision: do the more mechanically honest thing
even though it costs more code, because the simpler alternative doesn't actually deliver
the guarantee it looks like it delivers. Re-resolving looks like it validates the
destination; it validates a hostname and then trusts DNS again a moment later. A
configurable denylist looks like flexibility; it's a footgun for the one subsystem where
"flexible" and "safe" are in tension. Full fair scheduling looks like the "correct" answer;
it solves a fairness problem this project doesn't have yet at the cost of a fairness
mechanism it would have to build and defend. The breaker numbers look arbitrary either way;
naming the tradeoff instead of pretending there's a formula is the more honest answer.

## Tradeoff accepted

IP pinning means every `send()` call resolves DNS synchronously (off the event loop via
`asyncio.to_thread`, but still a real blocking syscall per attempt) and opens a fresh
`httpx.AsyncClient` scoped to that one request's pinned transport, rather than reusing one
persistent client with connection pooling across attempts the way Phase 2's adapter did.
Slower per-attempt and no cross-attempt keep-alive reuse to a given destination -- accepted
because the guarantee that matters here (never connecting to whatever DNS says *right now*)
only holds if resolution and connection happen as one atomic-enough unit, and pooling a
connection across multiple logical attempts would reopen exactly the rebinding question
pinning exists to close.

The per-endpoint semaphore is a real ceiling on that one endpoint's throughput even when
the rest of the system is idle -- three concurrent slow-but-healthy deliveries to the same
destination is enough to demonstrate the mechanism, not enough to be a serious constraint
in a portfolio-scale deployment, but it would be if this were actually handling one
customer's high-volume endpoint. Documented as a known limitation rather than tuned away,
since tuning it away is exactly the fair-scheduling project scoped out above.

Persisting `breaker_state`/`consecutive_failures`/`opened_at` on the endpoint row (rather
than, say, Redis, which is already in the stack and would avoid a Postgres write on every
attempt) means the breaker state read at the top of `DeliveryAttemptService.attempt()` and
the state written at the bottom are in the same transaction as the delivery_attempts row
and the Delivery state change -- a concurrent reader can never observe a delivery outcome
without the breaker transition it caused, or vice versa. The cost is one more UPDATE per
attempt on a table that's also being read by every other in-flight attempt against the same
endpoint; acceptable at this project's scale, and the same "given this scale, don't reach
for a second store" reasoning that kept the retry ZSET in Redis rather than a table.
