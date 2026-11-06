"""drop branch expiry, which only the removed public demo needed

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(op.f("ix_branches_expires_at"), table_name="branches")
    op.drop_column("branches", "expires_at")


def downgrade() -> None:
    op.add_column("branches", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_branches_expires_at"), "branches", ["expires_at"])
