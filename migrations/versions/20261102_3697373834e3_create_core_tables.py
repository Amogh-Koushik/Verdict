"""create core tables

Revision ID: 3697373834e3
Revises:
Create Date: 2026-11-02 16:02:03.059575
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3697373834e3"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("ends_at > starts_at", name=op.f("ck_contests_ends_after_start")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contests")),
        sa.UniqueConstraint("slug", name=op.f("uq_contests_slug")),
    )
    op.create_table(
        "problems",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("statement_md", sa.Text(), server_default="", nullable=False),
        sa.Column("time_limit_ms", sa.Integer(), server_default=sa.text("1000"), nullable=False),
        sa.Column("memory_limit_mb", sa.Integer(), server_default=sa.text("256"), nullable=False),
        sa.Column("tests_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_public", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("memory_limit_mb > 0", name=op.f("ck_problems_memory_limit_positive")),
        sa.CheckConstraint(
            "tests_version >= 0", name=op.f("ck_problems_tests_version_not_negative")
        ),
        sa.CheckConstraint("time_limit_ms > 0", name=op.f("ck_problems_time_limit_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_problems")),
        sa.UniqueConstraint("slug", name=op.f("uq_problems_slug")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(
        "uq_users_username_lower", "users", [sa.literal_column("lower(username)")], unique=True
    )
    op.create_table(
        "contest_problems",
        sa.Column("contest_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=1), nullable=False),
        sa.Column("problem_id", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "label ~ '^[A-Z]$'", name=op.f("ck_contest_problems_label_single_letter")
        ),
        sa.ForeignKeyConstraint(
            ["contest_id"],
            ["contests.id"],
            name=op.f("fk_contest_problems_contest_id_contests"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["problem_id"], ["problems.id"], name=op.f("fk_contest_problems_problem_id_problems")
        ),
        sa.PrimaryKeyConstraint("contest_id", "label", name=op.f("pk_contest_problems")),
        sa.UniqueConstraint(
            "contest_id", "problem_id", name=op.f("uq_contest_problems_contest_id_problem_id")
        ),
    )
    op.create_table(
        "problem_tests",
        sa.Column("problem_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("output_sha256", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["problem_id"],
            ["problems.id"],
            name=op.f("fk_problem_tests_problem_id_problems"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("problem_id", "version", "idx", name=op.f("pk_problem_tests")),
    )
    op.create_table(
        "submissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("problem_id", sa.Integer(), nullable=False),
        sa.Column("contest_id", sa.Integer(), nullable=True),
        sa.Column(
            "language",
            sa.Enum("cpp", "python", name="language", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "judging",
                "done",
                "failed",
                name="status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="queued",
            nullable=False,
        ),
        sa.Column(
            "verdict",
            sa.Enum(
                "AC",
                "WA",
                "TLE",
                "MLE",
                "RE",
                "CE",
                "SE",
                name="verdict",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("failed_test", sa.Integer(), nullable=True),
        sa.Column("time_ms", sa.Integer(), nullable=True),
        sa.Column("memory_kb", sa.Integer(), nullable=True),
        sa.Column("compile_output", sa.Text(), nullable=True),
        sa.Column("tests_version", sa.Integer(), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("judged_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt >= 0", name=op.f("ck_submissions_attempt_not_negative")),
        sa.CheckConstraint(
            "octet_length(compile_output) <= 4096", name=op.f("ck_submissions_compile_output_size")
        ),
        sa.CheckConstraint(
            "octet_length(source) <= 65536", name=op.f("ck_submissions_source_size")
        ),
        sa.ForeignKeyConstraint(
            ["contest_id"], ["contests.id"], name=op.f("fk_submissions_contest_id_contests")
        ),
        sa.ForeignKeyConstraint(
            ["problem_id"], ["problems.id"], name=op.f("fk_submissions_problem_id_problems")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_submissions_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submissions")),
    )
    op.create_index(
        "ix_submissions_contest_user_created",
        "submissions",
        ["contest_id", "user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_submissions_pending",
        "submissions",
        ["status", "id"],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'judging')"),
    )
    op.create_index(
        "ix_submissions_user_recent",
        "submissions",
        ["user_id", sa.literal_column("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_submissions_user_recent", table_name="submissions")
    op.drop_index(
        "ix_submissions_pending",
        table_name="submissions",
        postgresql_where=sa.text("status IN ('queued', 'judging')"),
    )
    op.drop_index("ix_submissions_contest_user_created", table_name="submissions")
    op.drop_table("submissions")
    op.drop_table("problem_tests")
    op.drop_table("contest_problems")
    op.drop_index("uq_users_username_lower", table_name="users")
    op.drop_table("users")
    op.drop_table("problems")
    op.drop_table("contests")
