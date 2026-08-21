// k6 load test harness for POST /v1/events -- the base script for the plan's load-test
// sweep (worker count x concurrent destinations x destination health), not a one-off
// script. It authenticates as a single tenant (via a pre-issued API key -- Relay has no
// login flow, auth is a static `X-API-Key` header) and posts events at a controlled
// arrival rate, each with a unique Idempotency-Key so every request is a genuinely new
// logical event rather than exercising the idempotency-replay path.
//
// Usage (single cell):
//   k6 run \
//     -e RELAY_BASE_URL=https://api.relay.bookr.tech \
//     -e RELAY_API_KEY=<key from scripts/load_test_setup.py> \
//     -e RELAY_EVENT_TYPE=load-test.ping \
//     -e RELAY_RATE=50 -e RELAY_DURATION=30s \
//     --summary-export=tests/load/results/summary.json \
//     tests/load/events_ingest.js
//
// See tests/load/README.md and tests/load/run_matrix.sh for running the full
// worker-count x destination-count x health-profile matrix the plan calls for.
//
// Throughput and latency are reported by k6's own end-of-run summary (http_reqs for
// throughput, http_req_duration for p50/p95/p99) plus the two custom metrics below;
// --summary-export writes the same numbers as machine-readable JSON for
// docs/load-test-results.md (a later pass, once each matrix cell has actually been run).

import http from "k6/http";
import { check } from "k6";
import { Counter, Trend } from "k6/metrics";

const BASE_URL = __ENV.RELAY_BASE_URL || "http://localhost:8080";
const API_KEY = __ENV.RELAY_API_KEY;
const EVENT_TYPE = __ENV.RELAY_EVENT_TYPE || "load-test.ping";
// Padded to roughly this many bytes so throughput numbers are comparable across runs
// regardless of the JSON scaffolding overhead.
const PAYLOAD_BYTES = Number(__ENV.RELAY_PAYLOAD_BYTES || 256);

const RATE = Number(__ENV.RELAY_RATE || 50);
const DURATION = __ENV.RELAY_DURATION || "30s";
const PRE_ALLOCATED_VUS = Number(__ENV.RELAY_PRE_ALLOCATED_VUS || 20);
const MAX_VUS = Number(__ENV.RELAY_MAX_VUS || 200);

if (!API_KEY) {
  throw new Error(
    "RELAY_API_KEY is required -- issue one with `uv run python scripts/load_test_setup.py " +
      "--base-url ...` (see tests/load/README.md), then pass it as -e RELAY_API_KEY=..."
  );
}

// constant-arrival-rate decouples offered load from response time: the point of this
// harness is to find the load at which latency and error rate start to break down, which
// a closed-loop (VU-count-based) executor would mask by naturally backing off.
export const options = {
  scenarios: {
    ingest: {
      executor: "constant-arrival-rate",
      rate: RATE,
      timeUnit: "1s",
      duration: DURATION,
      preAllocatedVUs: PRE_ALLOCATED_VUS,
      maxVUs: MAX_VUS,
    },
  },
  thresholds: {
    // A soft correctness check, not a hard pass/fail gate for the load-test sweep itself
    // -- the 50%-failing destination profile is expected to raise the *delivery* error
    // rate downstream, but ingestion (this script's only concern) should still mostly
    // succeed since it's independent of destination health.
    http_req_failed: ["rate<0.05"],
  },
};

const acceptedCount = new Counter("relay_events_accepted_total");
const rejectedCount = new Counter("relay_events_rejected_total");
const ingestLatency = new Trend("relay_ingest_latency_ms", true);

const FILLER = "x".repeat(Math.max(0, PAYLOAD_BYTES - 64));

function uniqueIdempotencyKey() {
  // Not cryptographically random -- just unique enough per (VU, iteration, wall clock)
  // that no two requests in a run collide. A real UUID would need either a jslib fetched
  // over the network at test time or k6's newer experimental webcrypto module; this
  // avoids both for a load-test-only key.
  return `k6-${__VU}-${__ITER}-${Date.now()}-${Math.floor(Math.random() * 1e9)}`;
}

function buildBody() {
  return JSON.stringify({
    type: EVENT_TYPE,
    payload: {
      seq: `${__VU}:${__ITER}`,
      filler: FILLER,
    },
  });
}

export default function () {
  const res = http.post(`${BASE_URL}/v1/events`, buildBody(), {
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
      "Idempotency-Key": uniqueIdempotencyKey(),
    },
    tags: { name: "ingest_event" },
  });

  ingestLatency.add(res.timings.duration);

  const accepted = check(res, {
    "status is 202": (r) => r.status === 202,
  });
  if (accepted) {
    acceptedCount.add(1);
  } else {
    rejectedCount.add(1);
  }
}
