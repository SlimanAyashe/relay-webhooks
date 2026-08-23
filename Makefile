.PHONY: up down logs migrate lint typecheck test build openapi backup restore-drill

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
	uv run mypy src

test:
	uv run pytest

build:
	docker build -f docker/Dockerfile -t relay:latest .

openapi:
	uv run python scripts/generate_openapi_spec.py

backup:
	uv run python scripts/backup_postgres.py

restore-drill:
	uv run python scripts/restore_drill.py
