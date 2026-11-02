from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.integration.forms import log_in_as_admin, register
from verdict.core.models import Contest, ContestProblem, Problem


def add_problem(db: Session, slug: str, *, is_public: bool, statement_md: str = "") -> Problem:
    problem = Problem(slug=slug, title=slug.title(), statement_md=statement_md, is_public=is_public)
    db.add(problem)
    db.commit()
    return problem


def add_to_contest(db: Session, problem: Problem, starts_in: timedelta) -> None:
    starts_at = datetime.now(UTC) + starts_in
    contest = Contest(
        slug=f"contest-{problem.slug}",
        title="Contest",
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=2),
    )
    db.add(contest)
    db.flush()
    db.add(ContestProblem(contest_id=contest.id, label="A", problem_id=problem.id))
    db.commit()


def test_anonymous_visitor_sees_public_problems_only(client: TestClient, db: Session) -> None:
    add_problem(db, "public", is_public=True)
    add_problem(db, "hidden", is_public=False)
    listing = client.get("/problems").text
    assert 'href="/problems/public"' in listing
    assert "hidden" not in listing
    assert client.get("/problems/public").status_code == 200
    assert client.get("/problems/hidden").status_code == 404


def test_logged_in_user_cannot_open_a_hidden_problem(client: TestClient, db: Session) -> None:
    add_problem(db, "hidden", is_public=False)
    register(client, "alice", "correct horse battery")
    response = client.get("/problems/hidden")
    assert response.status_code == 404
    assert "<h1>Not Found</h1>" in response.text
    assert "alice" in response.text, "the error page still shows who is logged in"


def test_problem_in_a_contest_that_has_started_is_visible(client: TestClient, db: Session) -> None:
    problem = add_problem(db, "contest-only", is_public=False)
    add_to_contest(db, problem, starts_in=-timedelta(minutes=5))
    assert 'href="/problems/contest-only"' in client.get("/problems").text
    assert client.get("/problems/contest-only").status_code == 200


def test_problem_in_a_contest_that_has_not_started_is_hidden(
    client: TestClient, db: Session
) -> None:
    problem = add_problem(db, "contest-only", is_public=False)
    add_to_contest(db, problem, starts_in=timedelta(minutes=5))
    assert "contest-only" not in client.get("/problems").text
    assert client.get("/problems/contest-only").status_code == 404


def test_admin_sees_hidden_problems_marked_as_not_public(client: TestClient, db: Session) -> None:
    add_problem(db, "hidden", is_public=False)
    log_in_as_admin(client, db)
    listing = client.get("/problems").text
    assert 'href="/problems/hidden"' in listing
    assert "Not public" in listing
    assert client.get("/problems/hidden").status_code == 200


def test_unknown_problem_is_not_found(client: TestClient) -> None:
    assert client.get("/problems/no-such-problem").status_code == 404


def test_problem_page_shows_the_rendered_statement_and_both_time_limits(
    client: TestClient, db: Session
) -> None:
    add_problem(db, "sum", is_public=True, statement_md="## Input\n\nTwo <b>integers</b>.")
    page = client.get("/problems/sum").text
    assert "<h2>Input</h2>" in page
    assert "&lt;b&gt;integers&lt;/b&gt;" in page
    assert "1 s for C++" in page
    assert "2 s for Python (2\N{MULTIPLICATION SIGN} the C++ limit)" in page
    assert "256 MB" in page
