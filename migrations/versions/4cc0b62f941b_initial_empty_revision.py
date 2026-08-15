"""initial empty revision

Revision ID: 4cc0b62f941b
Revises:
Create Date: 2026-08-15 04:25:00.905028

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "4cc0b62f941b"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
