# 0006. Phase 4 demo console decisions

## Decision

Four decisions define Phase 4's shape: the demo console is server-rendered Jinja2 +
htmx + Alpine.js + vanilla JS, with no SPA build step, served by the same FastAPI app as
the API; the sandbox tenant/key mechanism reuses Phase 1's API-key issuance and Phase
3's rate limiter rather than building parallel machinery, distinguished only by a new
`Tenant.is_sandbox` flag and an `ApiKey.expires_at` column; the live attempt timeline is
Server-Sent Events over one Redis Pub/Sub channel per tenant, with the sandbox key passed
as a `?api_key=` query parameter on that one connection because a browser's native
`EventSource` cannot set request headers; and `Settings.outbound_{connect,read}_timeout_seconds`
replace what were hardcoded 10-second module constants in `HttpxOutboundSender`, tightened
to 5 seconds so the console's `slow-8s` mock receiver reliably demonstrates the timeout
attempt path.

## Context

Phases 1-3 built a delivery engine and a real security story (signing, SSRF guard,
breaker, DLQ) that a resume bullet can claim but an interviewer can't *see* without
reading code. Phase 4's job is to make every one of those claims watchable in under a
minute by someone with no account and no context — which means the console has to be a
real, if narrow, second class of API client: it needs its own identity (the sandbox),
its own transport for live updates (SSE), and destinations that fail in interesting,
reproducible ways (the mock receivers) — all without weakening any guarantee Phase 3
just finished establishing, since this is also the first time the service is reachable
by someone who isn't a trusted tenant.

## Alternatives considered

- **A React/Vite SPA console** instead of server-rendered templates: better frontend
  resume signal, but a second build pipeline, a second CI job, and a second deployable
  artifact for a backend-focused project whose thesis is the delivery engine, not the
  frontend. htmx + Alpine.js gets interactivity (live timeline, forms, polling) without a
  build step; the console is still just a browser client of the real `/v1` API (plus
  `/v1/sandbox`) — nothing in it is reachable any other way, so swapping in a React
  frontend later would change zero backend code. The project plan (kept outside this repo,
  per `docs/PROJECT_STATUS.md`) makes this call explicitly; this ADR exists to record the
  reasoning, not merely restate the decision.
- **A parallel "sandbox" auth/quota system** (its own key format, its own tenant table)
  instead of reusing Phase 1/3 machinery: would keep sandbox concerns fully isolated from
  real-tenant code paths, but at the cost of two auth systems to reason about and keep in
  sync. Chosen instead: a sandbox tenant is a `tenants` row with `is_sandbox=true`, and a
  sandbox key is an `api_keys` row with `expires_at` set (every other key has
  `expires_at=NULL`, and never expires). `relay.api.auth._authenticate` grew one extra
  `is_expired()` check next to the existing `is_revoked()` one; the per-tenant rate
  limiter (`relay.infra.rate_limit.allow`) is reused with a tighter rate/burst pair
  instead of a second limiter. The only genuinely new mechanism is the fixed
  endpoint/event *count* caps (`relay.domain.sandbox.quota`), because nothing in Phases
  1-3 needed a "how many rows does this tenant already have" check.
- **WebSockets instead of SSE** for the live timeline: bidirectional, but the console
  never needs to send anything over that channel — every action (register an endpoint,
  trigger an event, replay from the DLQ) is already a plain `fetch()` against `/v1/*`.
  SSE is the simpler protocol for a strictly server-to-client feed, reconnects
  automatically via the browser's native `EventSource`, and needs no extra dependency.
  The cost: `EventSource` cannot set custom headers, so the sandbox key travels as a
  `?api_key=` query parameter on that one endpoint (`relay.api.auth.require_scope_allow_query_key`)
  rather than the `X-API-Key` header every other route requires — a real, accepted
  tradeoff (URLs land in access logs and browser history) scoped to one read-only,
  60-minute-lived stream, not a precedent applied anywhere else.
- **One shared Redis Pub/Sub channel, filtered per subscriber** instead of one channel
  per tenant id: filtering in application code is exactly the kind of thing that's easy
  to get right in the common case and silently wrong under a code change six months
  later. `relay.infra.attempt_events` keys the channel itself on `tenant_id`
  (`relay:attempts:tenant:{id}`), so cross-tenant isolation is a property of *which
  channel a subscriber is on*, not a filter it has to remember to apply — verified in
  `tests/integration/test_attempt_events.py::test_subscriber_receives_only_events_published_for_its_own_tenant`
  by publishing to two tenants concurrently and asserting one subscriber sees only its own.
- **Leaving the outbound HTTP timeout at Phase 2/3's hardcoded 10 seconds** (matching the
  plan's own "10s connect/read timeouts" line under abuse controls) instead of tightening
  it: the plan's `slow-8s` mock receiver is supposed to demonstrate the timeout attempt
  path, but an 8-second response comfortably clears a 10-second read timeout — it would
  just be a slow *success*, not a demonstrable timeout. Rather than rename the receiver
  or leave the demo not actually working as specified, `HttpxOutboundSender`'s timeouts
  moved from hardcoded module constants to `Settings.outbound_connect_timeout_seconds` /
  `outbound_read_timeout_seconds` (default 5s each) — a real behavior change to the
  delivery engine, not console-only, made because 5 seconds is still generous for any
  real webhook receiver and the value is now a documented, tunable setting instead of a
  constant nothing could override.

## Why

Every one of these is "reuse the mechanism that already exists and is already tested,
add the smallest new thing that's actually missing." The sandbox is not a different kind
of tenant as far as auth, rate limiting, or the delivery engine are concerned — it is a
tenant with an expiring key and two extra counts checked at write time. The console is
not a different kind of client as far as the API is concerned — it is a browser doing
what `curl` could do, plus one stream. Making that literally true (no console-only
backend code path except the mock receivers, `/v1/sandbox`, and the SSE/metrics
endpoints that expose data every other route already has access to) is what keeps this
phase from becoming a second application bolted onto the first one.

## Tradeoff accepted

A sandbox key in the SSE URL is a real, if narrow, secret-in-a-URL exposure — proxies,
browser history, and referrer headers on that one connection can see it. Accepted
because the key is already tightly scoped (max 3 endpoints, max 20 events, 1 req/s), TTL-limited
to 60 minutes, and this is the only route it's ever passed to that way; every other
route still requires the `X-API-Key` header. A production system serving real tenants
would need a different answer here (a short-lived, stream-specific token minted
server-side, say) — named explicitly as a known limitation rather than quietly
generalized to "query-param auth is fine."

Tightening the default outbound timeout from 10s to 5s is a real behavior change for
every delivery, not just console-triggered ones: a real destination that legitimately
takes 6-9 seconds to respond, which would have succeeded under the old default, now
times out and retries instead. Accepted because 5 seconds is still comfortably above
what a healthy webhook receiver should take, and because the alternative — a demo mock
receiver whose name promises a timeout it can't actually produce — is a worse failure to
ship in a project whose whole thesis is that its claims are demonstrable, not asserted.
