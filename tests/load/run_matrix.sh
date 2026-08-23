#!/usr/bin/env bash
# Runs the plan's load-test sweep: worker count (1, 4, 8) x concurrent destinations
# (10, 100) x destination health (healthy, failing) -- 12 cells total by default. This is
# the orchestrator that ties together docker compose's worker scaling,
# scripts/load_test_setup.py's fixture provisioning, and tests/load/events_ingest.js.
#
# It does NOT analyze results or write docs/load-test-results.md -- that's a later,
# separate pass (once each cell has actually been run against a real deployment and the
# Phase 5 queue-depth metric exists to fill in the "queue depth per cell" column). This
# script's job is to make running any one cell, or the whole matrix, a single reproducible
# command instead of a checklist of manual steps.
#
# Prerequisites:
#   - k6 installed (https://k6.io/docs/get-started/installation/) and on PATH.
#   - uv installed, `uv sync` already run (this script shells out to `uv run`).
#   - RELAY_BASE_URL: an HTTPS base URL whose /mock/* router is reachable, e.g. the
#     deployed instance (https://api.relay.bookr.tech). Endpoint registration rejects
#     non-https URLs (relay.services.endpoints.service.EndpointService._validate_url),
#     and the local Compose stack's Caddy only terminates plain HTTP -- see
#     tests/load/README.md for why this script can't just default to localhost.
#   - The worker containers scaled by this script (relay-worker, dispatcher, scheduler,
#     reaper) must be able to reach RELAY_BASE_URL themselves to actually deliver events
#     there -- true for the deployed instance, not for an arbitrary localhost URL typed in
#     from the host.
#
# Usage:
#   RELAY_BASE_URL=https://api.relay.bookr.tech tests/load/run_matrix.sh
#
# Override any axis (space-separated) or the k6 load shape via env vars:
#   WORKER_COUNTS="1 4 8"     DEST_COUNTS="10 100"     PROFILES="healthy failing"
#   RELAY_RATE=50             RELAY_DURATION=30s
#
# Run a single cell directly instead of the full matrix:
#   tests/load/run_matrix.sh --workers 4 --destinations 10 --profile failing

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESULTS_DIR="${REPO_ROOT}/tests/load/results"
COMPOSE_FILE="${REPO_ROOT}/docker/compose.yml"

WORKER_COUNTS="${WORKER_COUNTS:-1 4 8}"
DEST_COUNTS="${DEST_COUNTS:-10 100}"
PROFILES="${PROFILES:-healthy failing}"
RELAY_RATE="${RELAY_RATE:-50}"
RELAY_DURATION="${RELAY_DURATION:-30s}"
RELAY_EVENT_TYPE="${RELAY_EVENT_TYPE:-load-test.ping}"

if [[ -z "${RELAY_BASE_URL:-}" ]]; then
  echo "RELAY_BASE_URL is required (an https:// base URL -- see the header comment of this script)." >&2
  exit 1
fi

single_workers=""
single_dest=""
single_profile=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --workers) single_workers="$2"; shift 2 ;;
    --destinations) single_dest="$2"; shift 2 ;;
    --profile) single_profile="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done
if [[ -n "$single_workers" || -n "$single_dest" || -n "$single_profile" ]]; then
  WORKER_COUNTS="${single_workers:?--workers is required when using single-cell mode}"
  DEST_COUNTS="${single_dest:?--destinations is required when using single-cell mode}"
  PROFILES="${single_profile:?--profile is required when using single-cell mode}"
fi

mkdir -p "$RESULTS_DIR"

scale_workers() {
  local workers="$1"
  echo "--- scaling relay-worker to ${workers} replica(s) ---"
  docker compose -f "$COMPOSE_FILE" up -d \
    --scale "relay-worker=${workers}" \
    postgres redis api relay-worker dispatcher scheduler reaper
}

run_cell() {
  local workers="$1" dest_count="$2" profile="$3"
  local cell="w${workers}-d${dest_count}-${profile}"
  local fixture="${RESULTS_DIR}/fixture-${cell}.json"
  local summary="${RESULTS_DIR}/summary-${cell}.json"

  echo "=== cell ${cell}: ${workers} worker(s), ${dest_count} destination(s), profile=${profile} ==="

  scale_workers "$workers"

  echo "--- provisioning tenant + ${dest_count} endpoint(s) (profile=${profile}) ---"
  (cd "$REPO_ROOT" && uv run python scripts/load_test_setup.py \
    --base-url "$RELAY_BASE_URL" \
    --count "$dest_count" \
    --profile "$profile" \
    --event-type "$RELAY_EVENT_TYPE" \
    --output "$fixture")

  local api_key
  api_key="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['api_key'])" "$fixture")"

  echo "--- running k6 against ${RELAY_BASE_URL} ---"
  k6 run \
    -e RELAY_BASE_URL="$RELAY_BASE_URL" \
    -e RELAY_API_KEY="$api_key" \
    -e RELAY_EVENT_TYPE="$RELAY_EVENT_TYPE" \
    -e RELAY_RATE="$RELAY_RATE" \
    -e RELAY_DURATION="$RELAY_DURATION" \
    --summary-export="$summary" \
    "${REPO_ROOT}/tests/load/events_ingest.js"

  echo "=== cell ${cell} done -- fixture: ${fixture}, summary: ${summary} ==="
}

for workers in $WORKER_COUNTS; do
  for dest_count in $DEST_COUNTS; do
    for profile in $PROFILES; do
      run_cell "$workers" "$dest_count" "$profile"
    done
  done
done

echo "All cells complete. Raw fixtures and k6 summaries are under ${RESULTS_DIR} (gitignored scratch output)."
