from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Self

from fastapi import Depends, Form

from verdict.core.models import Contest
from verdict.web.problem_form import MAX_SLUG_LENGTH, MAX_TITLE_LENGTH, SLUG_PATTERN
from verdict.web.templating import IST

# What a datetime-local input sends and expects back.
LOCAL_TIME_FORMAT = "%Y-%m-%dT%H:%M"


class ContestFormError(ValueError):
    """A contest form value that can't be saved; the message is shown to the admin."""


@dataclass(frozen=True)
class ContestForm:
    """The contest form as typed, so a rejected form can be shown again unchanged."""

    slug: str = ""
    title: str = ""
    # Times are entered in IST, the time zone every page shows.
    starts_at: str = ""
    ends_at: str = ""

    @classmethod
    def from_contest(cls, contest: Contest) -> Self:
        return cls(
            slug=contest.slug,
            title=contest.title,
            starts_at=contest.starts_at.astimezone(IST).strftime(LOCAL_TIME_FORMAT),
            ends_at=contest.ends_at.astimezone(IST).strftime(LOCAL_TIME_FORMAT),
        )

    def apply_to(self, contest: Contest) -> None:
        """Validate every field, then copy them onto `contest`; nothing changes on error."""
        if len(self.slug) > MAX_SLUG_LENGTH or not SLUG_PATTERN.fullmatch(self.slug):
            raise ContestFormError(
                "The slug is used in the address: lowercase letters and digits, "
                f"separated by single hyphens, at most {MAX_SLUG_LENGTH} characters."
            )
        if not 1 <= len(self.title) <= MAX_TITLE_LENGTH:
            raise ContestFormError(f"The title must be 1 to {MAX_TITLE_LENGTH} characters.")
        starts_at = _parse_ist("Start", self.starts_at)
        ends_at = _parse_ist("End", self.ends_at)
        if ends_at <= starts_at:
            raise ContestFormError("The contest must end after it starts.")
        contest.slug = self.slug
        contest.title = self.title
        contest.starts_at = starts_at
        contest.ends_at = ends_at


def _parse_ist(label: str, raw: str) -> datetime:
    try:
        moment = datetime.strptime(raw, LOCAL_TIME_FORMAT)
    except ValueError:
        raise ContestFormError(f"{label} must be a date and time.") from None
    return moment.replace(tzinfo=IST)


FormField = Annotated[str, Form()]


def read_contest_form(
    slug: FormField = "",
    title: FormField = "",
    starts_at: FormField = "",
    ends_at: FormField = "",
) -> ContestForm:
    return ContestForm(
        slug=slug.strip(),
        title=title.strip(),
        starts_at=starts_at.strip(),
        ends_at=ends_at.strip(),
    )


ContestFormDep = Annotated[ContestForm, Depends(read_contest_form)]
