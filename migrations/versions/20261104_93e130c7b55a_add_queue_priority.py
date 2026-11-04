"""add queue priority

Revision ID: 93e130c7b55a
Revises: 3697373834e3
Create Date: 2026-11-04 18:10:40.840971
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "93e130c7b55a"
down_revision: str | Sequence[str] | None = "3697373834e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PENDING = sa.text("status IN ('queued', 'judging')")


def upgrade() -> None:
    # Existing rows become new submissions (1), the higher priority.
    op.add_column(
        "submissions",
        sa.Column("priority", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_submissions_priority_known"), "submissions", "priority IN (0, 1)"
    )
    op.drop_index("ix_submissions_pending", table_name="submissions", postgresql_where=PENDING)
    op.create_index(
        "ix_submissions_pending",
        "submissions",
        [sa.text("priority DESC"), "id"],
        unique=False,
        postgresql_where=PENDING,
    )


def downgrade() -> None:
    op.drop_index("ix_submissions_pending", table_name="submissions", postgresql_where=PENDING)
    op.create_index(
        "ix_submissions_pending",
        "submissions",
        ["status", "id"],
        unique=False,
        postgresql_where=PENDING,
    )
    op.drop_constraint(op.f("ck_submissions_priority_known"), "submissions", type_="check")
    op.drop_column("submissions", "priority")
