from datetime import datetime
from enum import StrEnum
from pathlib import Path

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from verdict.core.db import Base
from verdict.core.verdicts import Language, QueuePriority, SubmissionStatus, Verdict

MAX_SOURCE_BYTES = 64 * 1024
MAX_COMPILE_OUTPUT_BYTES = 4 * 1024


def _string_enum(enum_class: type[StrEnum], name: str) -> Enum:
    # A VARCHAR with a CHECK constraint is easier to extend later than a native Postgres ENUM.
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda members: [member.value for member in members],
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32))
    password_hash: Mapped[str] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


# Usernames keep the case they were registered with but are unique ignoring case.
Index("uq_users_username_lower", func.lower(User.username), unique=True)


class Problem(Base):
    __tablename__ = "problems"
    __table_args__ = (
        CheckConstraint("time_limit_ms > 0", name="time_limit_positive"),
        CheckConstraint("memory_limit_mb > 0", name="memory_limit_positive"),
        CheckConstraint("tests_version >= 0", name="tests_version_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    statement_md: Mapped[str] = mapped_column(Text, default="", server_default="")
    time_limit_ms: Mapped[int] = mapped_column(default=1000, server_default=text("1000"))
    memory_limit_mb: Mapped[int] = mapped_column(default=256, server_default=text("256"))
    tests_version: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    is_public: Mapped[bool] = mapped_column(default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    def time_limit_ms_for(self, language: Language, python_multiplier: float) -> int:
        if language is Language.PYTHON:
            return round(self.time_limit_ms * python_multiplier)
        return self.time_limit_ms


def problem_tests_dir(problems_dir: Path, problem_id: int, version: int) -> Path:
    """Where version `version` of a problem's tests is stored: {id}/v{version}/{i}.in and .out."""
    return problems_dir / str(problem_id) / f"v{version}"


class ProblemTest(Base):
    __tablename__ = "problem_tests"

    problem_id: Mapped[int] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(primary_key=True)
    idx: Mapped[int] = mapped_column(primary_key=True)
    input_sha256: Mapped[str] = mapped_column(String(64))
    output_sha256: Mapped[str] = mapped_column(String(64))


class ContestPhase(StrEnum):
    UPCOMING = "upcoming"
    RUNNING = "running"
    ENDED = "ended"


class Contest(Base):
    __tablename__ = "contests"
    __table_args__ = (CheckConstraint("ends_at > starts_at", name="ends_after_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    def phase_at(self, now: datetime) -> ContestPhase:
        """The window includes its start and excludes its end."""
        if now < self.starts_at:
            return ContestPhase.UPCOMING
        if now < self.ends_at:
            return ContestPhase.RUNNING
        return ContestPhase.ENDED


class ContestProblem(Base):
    __tablename__ = "contest_problems"
    __table_args__ = (
        UniqueConstraint("contest_id", "problem_id"),
        CheckConstraint("label ~ '^[A-Z]$'", name="label_single_letter"),
    )

    contest_id: Mapped[int] = mapped_column(
        ForeignKey("contests.id", ondelete="CASCADE"), primary_key=True
    )
    label: Mapped[str] = mapped_column(String(1), primary_key=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id"))


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        CheckConstraint(f"octet_length(source) <= {MAX_SOURCE_BYTES}", name="source_size"),
        CheckConstraint(
            f"octet_length(compile_output) <= {MAX_COMPILE_OUTPUT_BYTES}",
            name="compile_output_size",
        ),
        CheckConstraint("attempt >= 0", name="attempt_not_negative"),
        CheckConstraint("priority IN (0, 1)", name="priority_known"),
        Index("ix_submissions_contest_user_created", "contest_id", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id"))
    contest_id: Mapped[int | None] = mapped_column(ForeignKey("contests.id"))
    language: Mapped[Language] = mapped_column(_string_enum(Language, "language"))
    source: Mapped[str] = mapped_column(Text)
    status: Mapped[SubmissionStatus] = mapped_column(
        _string_enum(SubmissionStatus, "status"),
        default=SubmissionStatus.QUEUED,
        server_default=SubmissionStatus.QUEUED.value,
    )
    verdict: Mapped[Verdict | None] = mapped_column(_string_enum(Verdict, "verdict"))
    failed_test: Mapped[int | None]
    time_ms: Mapped[int | None]
    memory_kb: Mapped[int | None]
    compile_output: Mapped[str | None] = mapped_column(Text)
    tests_version: Mapped[int | None]
    attempt: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    priority: Mapped[int] = mapped_column(
        SmallInteger, default=QueuePriority.NEW, server_default=text(str(QueuePriority.NEW.value))
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None]
    judged_at: Mapped[datetime | None]

    @property
    def is_pending(self) -> bool:
        """True while the submission waits for, or is in, a judge."""
        return self.status in (SubmissionStatus.QUEUED, SubmissionStatus.JUDGING)


# In the claim's order, so the next submission to judge is the first entry of a small index.
Index(
    "ix_submissions_pending",
    Submission.priority.desc(),
    Submission.id,
    postgresql_where=text("status IN ('queued', 'judging')"),
)
Index("ix_submissions_user_recent", Submission.user_id, Submission.created_at.desc())
