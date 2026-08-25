# 0009. Phase 8 live verification decisions

## Decision

Four decisions define Phase 8's shape: verification runs against the deployed production stack
through its public surface (a sandbox tenant on the real domain) rather than a staging clone;
crash-recovery claims are proven by killing real containers on the compose stack (`docker kill`,
`docker restart`) rather than by injected fakes or a fault proxy; the live suite is
guarantee-pinned and deliberately small -- one test per promise in `docs/guarantees.md`, plus an
audit that maps the plan's twelve failure scenarios to named tests -- rather than a broad
regression sweep against prod; and operational claims (deploy abort, rollback, backup restore)
are verified by executed, dated drills rather than by the existence of scripts.

## Context

Phases 0-7 built the guarantees and tested them in CI against testcontainers. What remains
unproven is everything CI cannot see: the reverse proxy's routing, the production environment
variables, the egress firewall, the backup timer, and the gap between "the suite passes against
disposable containers" and "the thing on the domain does what the README claims." The interview
question this phase answers is "how do you know it works in production?" -- and "the unit tests
pass" is the answer that question exists to disqualify. Quality over quantity is the explicit
constraint: each test here must prove a claim the existing suites structurally cannot.

## Alternatives considered

- **A staging environment mirroring prod** instead of testing production directly: the
  respectable-by-default choice, but it doubles infrastructure on a single-VPS project, and a
  staging clone drifts from prod in exactly the details (proxy config, firewall rules, timers)
  this phase exists to verify. The sandbox tenancy built in Phase 4 already provides the
  isolation staging would buy: scoped keys, hard quotas, TTL'd data. Testing prod through the
  same door an interviewer uses is the point, not a compromise.
- **Toxiproxy or in-process fault injection** instead of killing containers: precise fault
  windows, deterministic timing, CI-friendly. But the claims under test are about process death
  -- "worker dies before ack" -- and a simulated network fault proves the code handles the
  simulation, not that a `kill -9` mid-delivery actually ends in `XAUTOCLAIM` redelivery. The
  honest cost: container kills give coarser timing, so the commit-vs-publish window is tested by
  stopping the relay *around* the window (event accepted while the relay is down) rather than
  crashing precisely inside it.
- **A broad regression suite against prod, or continuous synthetic monitoring**: more coverage
  on paper, but sandbox quotas rate-limit how much prod traffic testing may generate, test data
  pollutes real metrics, and piling up live tests is precisely the quantity-over-quality failure
  mode this phase rejects. CI owns breadth; the live suite owns the truth of the promises in
  `docs/guarantees.md`, and pinning it to that document keeps its size justified line by line.
- **Trusting scripted ops without drills**: the backup script, health-gated deploy, and rollback
  one-liner all exist as code, and it is tempting to count that as done. But "the restore has
  never been run against a production dump" is the canonical ops failure, and the plan already
  requires the restore test be *executed*. The same logic extends to the deploy gate: deploying
  a deliberately broken image once, and watching the swap abort, is the only evidence that the
  gate gates.

## Tradeoff accepted

Testing in production writes test data into real tables and real metrics -- accepted because
sandbox tenancy bounds the blast radius and TTL cleanup removes the data, but the p95 charts
will honestly show a smoke-test blip after every deploy, and that is documented rather than
hidden.

Chaos-by-container-kill is not per-PR CI material: it is slower, timing-sensitive, and needs the
full compose stack, so it runs on demand and before releases rather than on every commit. A
regression in crash recovery can therefore land on `main` and be caught later than a unit-level
regression would be -- accepted because the alternative (simulated faults in CI) catches a
different, weaker class of bug, and the integration suite still runs per-PR underneath.

A guarantee-pinned suite means regressions outside the pinned promises can reach prod without
tripping a live test -- accepted deliberately: the existing unit and integration suites already
cover breadth, and this phase optimizes for depth of proof on the seven bulletproof claims. When
a live test fails, the failure is by construction a broken promise, which is exactly what makes
each one worth narrating in an interview.
