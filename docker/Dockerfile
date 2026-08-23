# syntax=docker/dockerfile:1
FROM python:3.12-slim@sha256:dd29372629eeba2dd003fd9e9d35a5b8236c44727875a0364254b5127af88e65 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Dependency layer cached separately from source: this file changes far less
# often than src/, so `docker build` skips the slow `uv sync` step on most
# code-only commits.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

FROM python:3.12-slim@sha256:dd29372629eeba2dd003fd9e9d35a5b8236c44727875a0364254b5127af88e65 AS runtime

RUN groupadd --system --gid 1000 relay \
    && useradd --system --uid 1000 --gid relay --no-create-home relay \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder --chown=relay:relay /app/.venv /app/.venv
COPY --chown=relay:relay src/ src/
COPY --chown=relay:relay alembic.ini ./
COPY --chown=relay:relay migrations/ migrations/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER relay
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

# --no-access-log: TraceIdMiddleware already logs one structured JSON line per request
# (method, path, status_code, duration_ms, correlation_id) -- uvicorn's own default access
# log would otherwise print a second, plain-text, non-JSON line for every request, directly
# undermining the "every log line is machine-parseable" goal this flag exists for.
CMD ["uvicorn", "relay.api.app:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
