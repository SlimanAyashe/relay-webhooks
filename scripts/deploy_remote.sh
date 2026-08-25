#!/usr/bin/env bash
# Runs ON the VPS (copied there by .github/workflows/deploy.yml, never run
# from CI directly) inside /opt/relay. Pulls the new image, migrates, swaps,
# and health-gates the swap: if /readyz never goes green, it rolls back to
# whatever image was running before and leaves that container up rather than
# leaving the deploy half-applied. See docs/adr/0001-phase-0-skeleton.md for
# why this is abort-and-roll-back instead of full deploy automation.
set -euo pipefail

IMAGE="$1" # e.g. ghcr.io/<owner>/<repo>:<sha>

# RELAY_SKIP_PULL=1 uses an image that is already on this host instead of pulling one.
# Exists for the deploy-gate drill (docs/runbook.md), which deliberately deploys a locally
# built image whose /readyz fails to watch the gate abort the swap -- never for a real
# deploy, where skipping the pull would silently ship whatever stale tag happens to be
# lying around.
if [ "${RELAY_SKIP_PULL:-0}" = "1" ]; then
    echo "RELAY_SKIP_PULL=1: using local image $IMAGE without pulling (drill only)."
    docker image inspect "$IMAGE" > /dev/null
else
    docker pull "$IMAGE"
fi

# Migrate before the swap so the new schema is in place before new code runs
# against it (expand/contract keeps this a non-event for compatible changes).
docker run --rm --env-file .env --network relay_default "$IMAGE" alembic upgrade head

PREVIOUS_IMAGE=$(docker inspect --format='{{.Config.Image}}' relay-api-1 2>/dev/null || echo "")

# The delivery-engine workers (Phase 2) share the api image and swap in lockstep with it --
# there's no separate versioning story for them yet, and api's /readyz (which checks
# Postgres + Redis) is used as the health gate for the whole swap since none of the workers
# serve HTTP to health-check individually.
SERVICES="api relay-worker dispatcher scheduler reaper"

# --env-file is passed explicitly (not left to Compose's automatic
# .env-in-cwd discovery) because that auto-discovery does not reliably find
# ../.env's POSTGRES_PASSWORD here for variable interpolation in the compose
# file itself (env_file: on the api service only injects vars into the
# container, it doesn't feed ${...} interpolation in the YAML).
RELAY_IMAGE="$IMAGE" docker compose --env-file .env -f docker/compose.prod.yml up -d $SERVICES

for _ in $(seq 1 20); do
    if curl -fs http://localhost:8000/readyz > /dev/null; then
        echo "Swap to $IMAGE healthy."
        exit 0
    fi
    sleep 3
done

echo "ERROR: /readyz never went green after deploying $IMAGE; aborting swap." >&2
if [ -n "$PREVIOUS_IMAGE" ]; then
    RELAY_IMAGE="$PREVIOUS_IMAGE" docker compose --env-file .env -f docker/compose.prod.yml up -d $SERVICES
    echo "Rolled back to $PREVIOUS_IMAGE; previous container left running." >&2
fi
exit 1
