import re
from dataclasses import dataclass
from typing import Annotated, Self

from fastapi import Depends, Form

from verdict.core.models import Problem

SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_SLUG_LENGTH = 64
MAX_TITLE_LENGTH = 200
MAX_STATEMENT_LENGTH = 100_000
TIME_LIMIT_RANGE_MS = (100, 10_000)
MEMORY_LIMIT_RANGE_MB = (16, 1024)


class ProblemFormError(ValueError):
    """A problem form value that can't be saved; the message is shown to the admin."""


@dataclass(frozen=True)
class ProblemForm:
    """The problem form as typed, so a rejected form can be shown again unchanged."""

    slug: str = ""
    title: str = ""
    statement_md: str = ""
    time_limit_ms: str = "1000"
    memory_limit_mb: str = "256"
    is_public: bool = False

    @classmethod
    def from_problem(cls, problem: Problem) -> Self:
        return cls(
            slug=problem.slug,
            title=problem.title,
            statement_md=problem.statement_md,
            time_limit_ms=str(problem.time_limit_ms),
            memory_limit_mb=str(problem.memory_limit_mb),
            is_public=problem.is_public,
        )

    def apply_to(self, problem: Problem) -> None:
        """Validate every field, then copy them onto `problem`; nothing changes on error."""
        if len(self.slug) > MAX_SLUG_LENGTH or not SLUG_PATTERN.fullmatch(self.slug):
            raise ProblemFormError(
                "The slug is used in the address: lowercase letters and digits, "
                f"separated by single hyphens, at most {MAX_SLUG_LENGTH} characters."
            )
        if not 1 <= len(self.title) <= MAX_TITLE_LENGTH:
            raise ProblemFormError(f"The title must be 1 to {MAX_TITLE_LENGTH} characters.")
        if len(self.statement_md) > MAX_STATEMENT_LENGTH:
            raise ProblemFormError(
                f"The statement must be at most {MAX_STATEMENT_LENGTH:,} characters."
            )
        time_limit_ms = _int_in_range("Time limit", self.time_limit_ms, TIME_LIMIT_RANGE_MS, "ms")
        memory_limit_mb = _int_in_range(
            "Memory limit", self.memory_limit_mb, MEMORY_LIMIT_RANGE_MB, "MB"
        )
        problem.slug = self.slug
        problem.title = self.title
        problem.statement_md = self.statement_md
        problem.time_limit_ms = time_limit_ms
        problem.memory_limit_mb = memory_limit_mb
        problem.is_public = self.is_public


def _int_in_range(label: str, raw: str, bounds: tuple[int, int], unit: str) -> int:
    low, high = bounds
    try:
        value = int(raw)
    except ValueError:
        raise ProblemFormError(f"{label} must be a whole number of {unit}.") from None
    if not low <= value <= high:
        raise ProblemFormError(f"{label} must be between {low} and {high} {unit}.")
    return value


FormField = Annotated[str, Form()]


def read_problem_form(
    slug: FormField = "",
    title: FormField = "",
    statement_md: FormField = "",
    time_limit_ms: FormField = "",
    memory_limit_mb: FormField = "",
    is_public: FormField = "",
) -> ProblemForm:
    return ProblemForm(
        slug=slug.strip(),
        title=title.strip(),
        # Browsers submit textareas with CRLF line endings; store plain LF.
        statement_md=statement_md.replace("\r\n", "\n"),
        time_limit_ms=time_limit_ms.strip(),
        memory_limit_mb=memory_limit_mb.strip(),
        is_public=is_public == "on",
    )


ProblemFormDep = Annotated[ProblemForm, Depends(read_problem_form)]
