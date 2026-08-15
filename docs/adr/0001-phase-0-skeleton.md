# 0001. Phase 0 stack and deploy decisions

## Decision

Use `uv` for dependency management, Alembic as the *only* mechanism that ever creates or changes
schema (no `Base.metadata.create_all`, anywhere, including tests), Caddy as the reverse proxy for
both local dev and production TLS, and a health-gated deploy that aborts and leaves the previous
container running if `/readyz` doesn't go green after the swap.

## Context

Phase 0's job is to get a real, empty skeleton into production before any feature work starts, so
every later phase ships into a stack that's already proven to deploy. That means the tooling
choices made here — package manager, migration tool, proxy, deploy strategy — are load-bearing for
the rest of the project, not just local conveniences.

## Alternatives considered

- **pip-tools / Poetry** instead of `uv`: both work, but `uv` is materially faster and its lockfile
  + `uv run` model collapses venv management into one tool, which matters more on a solo project
    with no CI cache warmed yet.
- **Letting SQLAlchemy `create_all()` bootstrap tables** instead of an initial Alembic revision:
  faster to type, but it creates two divergent paths to the same schema (dev convenience vs. real
  migration) and hides schema drift until it breaks in an environment that only runs migrations.
- **Nginx + certbot** instead of Caddy: more configuration surface for the same outcome — Caddy's
  automatic ACME renewal is a smaller, equally defensible choice for a single-domain deploy.
- **Automatic rollback on failed deploy** instead of abort-and-leave-previous-running: full
  automatic rollback is real engineering work for a failure mode (a bad deploy) that's rare and
  has a documented one-line manual fix (`deploy.sh <previous-sha>`).

## Tradeoff accepted

Alembic-only means every schema change, even trivial ones, requires writing a migration — slower
than letting the ORM improvise, but it's the only way "the migration that ran in CI is the
migration that ran in prod" stays true. Abort-on-fail (not auto-rollback) accepts a few minutes of
manual intervention on the rare failed deploy in exchange for not building rollback automation
before there's a second environment that would ever exercise it.
