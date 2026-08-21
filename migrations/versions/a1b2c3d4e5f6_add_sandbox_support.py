"""add sandbox support: tenants.is_sandbox, api_keys.expires_at, delivery_attempts.request_headers

Revision ID: a1b2c3d4e5f6
Revises: cac78d3eba17
Create Date: 2026-08-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "cac78d3eba17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "tenants",
        sa.Column("is_sandbox", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "api_keys",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "delivery_attempts",
        sa.Column("request_headers", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("delivery_attempts", "request_headers")
    op.drop_column("api_keys", "expires_at")
    op.drop_column("tenants", "is_sandbox")
