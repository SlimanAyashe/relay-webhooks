from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all Phase 1+ ORM models. Alembic autogenerate diffs against
    this metadata — every new model module must be imported in migrations/env.py so its table
    registers before `alembic revision --autogenerate` runs.
    """
