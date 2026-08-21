# Guarantees

What Relay promises, and exactly where each promise stops. Good distributed-systems work
isn't "what does my system do," it's "what does it promise, and where does the promise
stop" -- both lists below are meant to be read together, not the "guaranteed" half alone.

## Guaranteed

- **An accepted (`202`) event is durably persisted before the response returns.** The
  event row and its outbox row commit in one Postgres transaction (`EventIngestService`);
  a crash between "the response was sent" and "the relay published it" is not a lost
  event, it's an outbox row still `pending` for the next relay run to find.
- **Delivery is at-least-once, never exactly-once.** Consumers must be idempotent. A
  worker that sends the HTTP request and dies before acking the stream message causes a
  genuine, observable duplicate delivery on redelivery -- this is by design, not a bug; see
  `docs/failure-modes.md`'s crash-recovery rows and `docs/adr/0004-phase-2-delivery-engine.md`.
- **Idempotency keys prevent duplicate logical ingestion.** The same `Idempotency-Key`
  with an identical body returns the original event, never a second row; a differing body
  is `409`, not a silent overwrite.
- **Retries use bounded exponential backoff with full jitter**, so a downstream recovering
  from an outage doesn't immediately get hit by every failed delivery's retry at once.
- **Deliveries that exhaust their retry budget land in the DLQ and stay replayable.**
  `deliveries WHERE state='dead'` is the DLQ (no separate table); replaying one
  (`POST /v1/deliveries/{id}/replay`) resets it to a fresh retry chain while the original
  `delivery_attempts` history is never modified and stays queryable via `GET /v1/dlq`.
- **Every outbound delivery request is HMAC-signed with a timestamp.** `X-Relay-Signature`
  is `HMAC-SHA256(endpoint_secret, f"{timestamp}.{body}")`, hex-encoded;
  `X-Relay-Timestamp` and `X-Relay-Delivery-Id` accompany it. A receiver that recomputes
  the same HMAC over the timestamp and the *exact bytes it received*, using its own copy of
  the endpoint secret, and compares constant-time (`hmac.compare_digest`), has proven two
  things: the payload wasn't altered in transit, and the request actually came from Relay
  (or someone who has the secret) -- not from an arbitrary caller who discovered the
  endpoint URL. Verification additionally rejects any timestamp more than
  `signature_tolerance_seconds` (default 300s) away from "now" in either direction, closing
  a captured-and-replayed-later request out of the trust window. What this does *not*
  prove: that the request was ever actually sent to *this* endpoint specifically, or that
  it wasn't replayed *within* the tolerance window by someone who captured it in flight --
  the tolerance window is a deliberate breadth-vs-safety tradeoff (see below), not zero.
- **Destinations are SSRF-restricted, not merely "checked."** Every delivery attempt --
  the first one and every retry, and every redirect hop, not only the initial URL --
  resolves the destination hostname and rejects loopback/RFC1918/link-local/CGNAT
  (100.64.0.0/10)/IPv6 ULA/the cloud metadata address (169.254.169.254) post-resolution,
  and rejects any port outside the configured allow-list (80/443 by default). A blocked
  destination is recorded as `error_class=ssrf_blocked` on the attempt; no connection is
  ever attempted against it. Redirects are not auto-followed by the HTTP client -- each
  `Location` is re-validated by the same guard, bounded to a small number of hops, before
  Relay ever connects to it.
- **The SSRF guard closes the DNS-rebinding TOCTOU window, not just narrows it.**
  Validating a hostname and then handing that *hostname* to the HTTP client is a bug: the
  client resolves again, and a second lookup can return a different address than the one
  validated. Relay's outbound adapter instead connects directly to the IP address the
  guard actually validated (`relay.infra.pinned_transport.PinnedAsyncTransport`),
  preserving the original `Host` header and TLS SNI so certificate verification still
  targets the real hostname. `tests/integration/test_http_sender_ssrf.py::test_dns_rebinding_connects_to_the_originally_validated_ip_not_a_fresh_lookup`
  proves this directly: a resolver returning a public address on the first lookup and a
  private one on a second still results in a connection to the first (validated) address,
  because there is no second lookup mid-request.
- **A dead/slow destination cannot exhaust the worker pool on its own.** The circuit
  breaker (5 consecutive failures by default) stops sending real requests to a destination
  that's actively failing; a per-endpoint concurrency cap (3 by default) bounds how many
  in-flight attempts any single endpoint can have even while it's still returning 2xx
  slowly; a process-wide dispatcher concurrency cap bounds total concurrent outbound
  deliveries across every endpoint regardless of how many are registered.
- **A sandbox tenant (self-provisioned via `POST /v1/sandbox`, no account needed) is
  hard-capped well below a real tenant's limits, on every axis independently: a 60-minute
  key TTL, a fixed max endpoint count, a fixed max event count, and a per-second rate
  limit tighter than the default tenant budget.** All four are enforced server-side
  regardless of what the console UI does or doesn't send; a sandbox key past its TTL is
  rejected exactly like a revoked one, and the two count caps
  (`relay.domain.sandbox.quota`) are checked independently of the rate limiter, not
  implied by it.
- **The console's mock receivers and sandbox surface never weaken any guarantee above.**
  `/mock/*` are ordinary registered destinations subject to the same SSRF guard, signing,
  breaker, and retry logic as any tenant's real endpoint; `/v1/sandbox/stream` only ever
  exposes attempt data already reachable via `GET /v1/dlq`/`GET /v1/deliveries`, scoped to
  the requesting tenant by construction (one Redis Pub/Sub channel per tenant id, not a
  filter applied after the fact).

## Not guaranteed

- **Exactly-once external side effects.** A timeout or connection reset after the request
  was written is indistinguishable, from Relay's side, from a request that never arrived --
  a retry may duplicate a side effect the receiver already applied. This is the same fact
  `docs/failure-modes.md`'s crash-recovery row pins down with a test.
- **Zero duplicate deliveries.** At-least-once means duplicates are possible by design, not
  a bug to be eliminated.
- **Global fairness across tenants, or full fairness across endpoints.** The per-endpoint
  concurrency cap stops one destination from starving the whole pool; it is not weighted
  fair queuing, and one tenant with many endpoints still gets more aggregate dispatcher
  capacity than one with few. See `docs/adr/0005-phase-3-security-resilience.md` for why
  that tradeoff was made deliberately rather than left as an oversight.
- **No ordering -- not even to a single endpoint.** Retries interleave with fresh traffic;
  if event A fails and backs off while event B to the same endpoint succeeds immediately, B
  arrives first.
- **Delivery to a destination that stays down past the retry budget.** It lands in the DLQ,
  replayable on demand, but Relay does not retry forever.
- **SSRF mitigation is not "SSRF is solved."** DNS rebinding *within a single request* is
  closed by IP pinning (proven above). What is not closed: a hostname that is genuinely,
  persistently public at delivery-attempt time but is reconfigured to point somewhere
  forbidden *before* the next retry attempt re-runs the guard is correctly caught on that
  next attempt (the guard re-validates every attempt, not just the first) -- but the
  network-layer egress rules described in `docs/runbook.md` are the deliberate second layer
  behind the application-layer guard, precisely because "the app's own validation code is
  the only thing standing between an attacker and the internal network" is not a claim this
  project is willing to make. A compromised or buggy resolver, or a validated destination
  that later 30x-redirects through a *non-HTTP* mechanism the guard doesn't see, are outside
  this guarantee's scope entirely.
- **HMAC signing does not guarantee freshness within the tolerance window.** A captured
  signed request replayed inside `signature_tolerance_seconds` (default 300s) still
  verifies -- the tolerance exists to absorb ordinary clock skew between Relay and a
  receiver, and 300 seconds is a width chosen for that purpose, not a zero-replay
  guarantee. A receiver with stricter replay requirements should additionally track
  `X-Relay-Delivery-Id` values it has already seen within the tolerance window.
- **The sandbox key is not treated as confidential on `GET /v1/sandbox/stream`.** A
  browser's native `EventSource` can't set request headers, so that one endpoint accepts
  the key as a `?api_key=` query parameter -- which can end up in proxy/access logs and
  browser history -- rather than only the `X-API-Key` header every other route requires.
  Deliberately scoped to a single, tightly-capped, 60-minute-lived, read-only stream; see
  `docs/adr/0006-phase-4-demo-console.md` for the tradeoff and why it isn't generalized.
- **The rate limiter is a per-tenant budget, not a global one, and it is best-effort under
  Redis unavailability.** If Redis is unreachable, `allow()`'s `EVAL` call raises like any
  other Redis error -- event ingest fails closed (the request errors out) rather than
  silently bypassing the limit, but this is not the same as a formally verified guarantee
  under partial Redis failure modes (e.g. a failover mid-script).

## Related reading

- `docs/failure-modes.md` -- the running "if it dies/misbehaves here, then what, and which
  test proves it" table, one row per guarantee above.
- `docs/adr/` -- the numbered decisions and their alternatives, including
  `docs/adr/0005-phase-3-security-resilience.md` for the signing/SSRF/breaker/concurrency
  tradeoffs specifically, and `docs/adr/0006-phase-4-demo-console.md` for the sandbox/SSE
  tradeoffs.
- `docs/runbook.md` -- the network-layer egress rules that back the SSRF guarantee as
  defense in depth, plus deploy/rollback/DLQ-drain operational steps.
