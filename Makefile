.PHONY: up down logs migrate lint typecheck test test-e2e test-chaos verify-egress build openapi backup restore-drill load-test

up:
	docker compose -f docker/compose.yml up -d --build

down:
	docker compose -f docker/compose.yml down

logs:
	docker compose -f docker/compose.yml logs -f

migrate:
	uv run alembic upgrade head

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy --strict src

test:
	uv run pytest

# Live suites -- they talk to a deployment over HTTP; point them at one.
# RELAY_E2E_BASE_URL defaults to http://localhost:8000; set RELAY_E2E_RECEIVER_BASE_URL to
# the deployment's public https origin so deliveries to the /mock/* receivers can be proven
# (Relay's own SSRF guard refuses a loopback or non-443 destination, by design).
test-e2e:
	uv run pytest tests/e2e -v

test-chaos:
	uv run pytest tests/chaos -v

verify-egress:
	uv run python scripts/verify_egress_firewall.py

build:
	docker build -f docker/Dockerfile -t relay:latest .

openapi:
	uv run python scripts/generate_openapi_spec.py

backup:
	uv run python scripts/backup_postgres.py

restore-drill:
	uv run python scripts/restore_drill.py

# Requires k6 (https://k6.io) and RELAY_BASE_URL (an https:// URL -- see
# tests/load/README.md for why). Runs the full worker-count x destination-count x
# health-profile matrix; see tests/load/run_matrix.sh for single-cell / per-axis overrides.
load-test:
	tests/load/run_matrix.sh
