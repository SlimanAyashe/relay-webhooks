"""add consecutive_failures to endpoints

Revision ID: cac78d3eba17
Revises: 157cc1f4495a
Create Date: 2026-08-16 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cac78d3eba17"
down_revision: str | Sequence[str] | None = "157cc1f4495a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "endpoints",
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("endpoints", "consecutive_failures")
