from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.integration.contests import add_contest, add_contest_problem
from tests.integration.forms import csrf_token_from, log_in_as_admin
from verdict.core.events import scoreboard_generation_key
from verdict.core.models import Contest, ContestProblem, Problem

CONTEST_FIELDS = {
    "slug": "weekly-1",
    "title": "Weekly contest 1",
    "starts_at": "2026-12-01T10:00",
    "ends_at": "2026-12-01T12:30",
}


@pytest.fixture
def admin(client: TestClient, db: Session) -> TestClient:
    log_in_as_admin(client, db)
    return client


def post_form(client: TestClient, path: str, fields: dict[str, str]) -> httpx2.Response:
    csrf_token = csrf_token_from(client.get("/admin/contests"))
    return client.post(path, data={**fields, "csrf_token": csrf_token}, follow_redirects=False)


def contest_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Contest)) or 0


def add_problem(db: Session, slug: str) -> Problem:
    problem = Problem(slug=slug, title=slug.title(), tests_version=1)
    db.add(problem)
    db.commit()
    return problem


def test_an_admin_creates_a_contest_with_times_typed_in_ist(admin: TestClient, db: Session) -> None:
    response = post_form(admin, "/admin/contests/new", CONTEST_FIELDS)

    contest = db.scalars(select(Contest)).one()
    assert (response.status_code, response.headers["location"]) == (
        303,
        f"/admin/contests/{contest.id}/edit",
    )
    assert contest.slug == "weekly-1"
    assert contest.starts_at == datetime(2026, 12, 1, 4, 30, tzinfo=UTC)
    assert contest.ends_at == datetime(2026, 12, 1, 7, 0, tzinfo=UTC)
    edit_page = admin.get(f"/admin/contests/{contest.id}/edit").text
    assert 'value="2026-12-01T10:00"' in edit_page
    assert "Weekly contest 1" in admin.get("/admin/contests").text


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        pytest.param({"slug": "Weekly 1"}, "The slug is used in the address", id="bad slug"),
        pytest.param({"title": ""}, "The title must be", id="empty title"),
        pytest.param({"starts_at": "soon"}, "Start must be a date and time.", id="bad start"),
        pytest.param(
            {"ends_at": "2026-12-01T09:00"},
            "The contest must end after it starts.",
            id="ends before it starts",
        ),
    ],
)
def test_an_invalid_contest_is_shown_again_with_the_reason(
    admin: TestClient, db: Session, changes: dict[str, str], message: str
) -> None:
    response = post_form(admin, "/admin/contests/new", {**CONTEST_FIELDS, **changes})
    assert response.status_code == 400
    assert message in response.text
    assert contest_count(db) == 0


def test_a_second_contest_with_the_same_slug_is_refused(admin: TestClient, db: Session) -> None:
    post_form(admin, "/admin/contests/new", CONTEST_FIELDS)
    response = post_form(admin, "/admin/contests/new", {**CONTEST_FIELDS, "title": "Another"})
    assert response.status_code == 409
    assert "Another contest already uses the slug &#39;weekly-1&#39;." in response.text
    assert contest_count(db) == 1


def test_editing_a_contest_drops_its_cached_scoreboard(
    admin: TestClient, db: Session, redis: Redis
) -> None:
    contest = add_contest(db, starts_in=timedelta(days=1))
    fields = {**CONTEST_FIELDS, "slug": "weekly", "title": "Renamed"}

    response = post_form(admin, f"/admin/contests/{contest.id}/edit", fields)

    assert response.status_code == 303
    db.refresh(contest)
    assert contest.title == "Renamed"
    assert redis.get(scoreboard_generation_key(contest.id)) == "1"


def test_an_admin_adds_and_removes_contest_problems(
    admin: TestClient, db: Session, redis: Redis
) -> None:
    contest = add_contest(db, starts_in=timedelta(days=1))
    problem = add_problem(db, "sum")
    path = f"/admin/contests/{contest.id}/problems"

    added = post_form(admin, path, {"label": "C", "problem_id": str(problem.id)})

    assert added.status_code == 303
    entry = db.scalars(select(ContestProblem)).one()
    assert (entry.contest_id, entry.label, entry.problem_id) == (contest.id, "C", problem.id)
    edit_page = admin.get(f"/admin/contests/{contest.id}/edit").text
    assert f'action="/admin/contests/{contest.id}/problems/C/remove"' in edit_page
    assert "<option>C</option>" not in edit_page, "a used label is not offered again"

    removed = post_form(admin, f"{path}/C/remove", {})

    assert removed.status_code == 303
    assert db.scalars(select(ContestProblem)).first() is None
    assert redis.get(scoreboard_generation_key(contest.id)) == "2"


@pytest.mark.parametrize(
    ("fields", "status_code", "message"),
    [
        pytest.param({"label": "A"}, 409, "Label A is already used", id="label in use"),
        pytest.param(
            {"label": "B", "problem": "first"}, 409, "already in this contest", id="problem in use"
        ),
        pytest.param({"label": "a"}, 400, "Choose a label from A to Z.", id="lowercase label"),
        pytest.param({"label": "AB"}, 400, "Choose a label from A to Z.", id="two letters"),
        pytest.param({"label": "B", "problem_id": "x"}, 400, "Choose a problem.", id="no problem"),
        pytest.param(
            {"label": "B", "problem_id": "999"}, 409, "doesn&#39;t exist", id="unknown problem"
        ),
    ],
)
def test_a_problem_that_cant_be_added_is_refused_with_the_reason(
    admin: TestClient, db: Session, fields: dict[str, str], status_code: int, message: str
) -> None:
    contest = add_contest(db, starts_in=timedelta(days=1))
    first = add_contest_problem(db, contest, "A", "first")
    second = add_problem(db, "second")
    chosen = first if fields.pop("problem", "second") == "first" else second
    form = {"problem_id": str(chosen.id), **fields}

    response = post_form(admin, f"/admin/contests/{contest.id}/problems", form)

    assert response.status_code == status_code
    assert message in response.text
    assert db.scalar(select(func.count()).select_from(ContestProblem)) == 1


def test_removing_a_label_the_contest_doesnt_have_is_not_found(
    admin: TestClient, db: Session
) -> None:
    contest = add_contest(db, starts_in=timedelta(days=1))
    response = post_form(admin, f"/admin/contests/{contest.id}/problems/Q/remove", {})
    assert response.status_code == 404
