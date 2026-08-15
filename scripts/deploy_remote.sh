#!/usr/bin/env bash
# Runs ON the VPS (copied there by .github/workflows/deploy.yml, never run
# from CI directly) inside /opt/relay. Pulls the new image, migrates, swaps,
# and health-gates the swap: if /readyz never goes green, it rolls back to
# whatever image was running before and leaves that container up rather than
# leaving the deploy half-applied. See docs/adr/0001-phase-0-skeleton.md for
# why this is abort-and-roll-back instead of full deploy automation.
set -euo pipefail

IMAGE="$1" # e.g. ghcr.io/<owner>/<repo>:<sha>

docker pull "$IMAGE"

# Migrate before the swap so the new schema is in place before new code runs
# against it (expand/contract keeps this a non-event for compatible changes).
docker run --rm --env-file .env --network relay_default "$IMAGE" alembic upgrade head

PREVIOUS_IMAGE=$(docker inspect --format='{{.Config.Image}}' relay-api-1 2>/dev/null || echo "")

# --env-file is passed explicitly (not left to Compose's automatic
# .env-in-cwd discovery) because that auto-discovery does not reliably find
# ../.env's POSTGRES_PASSWORD here for variable interpolation in the compose
# file itself (env_file: on the api service only injects vars into the
# container, it doesn't feed ${...} interpolation in the YAML).
RELAY_IMAGE="$IMAGE" docker compose --env-file .env -f docker/compose.prod.yml up -d api

for _ in $(seq 1 20); do
    if curl -fs http://localhost:8000/readyz > /dev/null; then
        echo "Swap to $IMAGE healthy."
        exit 0
    fi
    sleep 3
done

echo "ERROR: /readyz never went green after deploying $IMAGE; aborting swap." >&2
if [ -n "$PREVIOUS_IMAGE" ]; then
    RELAY_IMAGE="$PREVIOUS_IMAGE" docker compose --env-file .env -f docker/compose.prod.yml up -d api
    echo "Rolled back to $PREVIOUS_IMAGE; previous container left running." >&2
fi
exit 1
