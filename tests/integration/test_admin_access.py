from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.integration.contests import add_contest
from tests.integration.forms import csrf_token_from, register
from verdict.core.config import Settings
from verdict.core.models import ContestProblem, Problem
from verdict.web.main import create_app


def admin_endpoints(settings: Settings, ids: dict[str, str]) -> list[tuple[str, str]]:
    """Every (method, path) under /admin, with real ids filled in."""
    paths = create_app(settings).openapi()["paths"]
    return [
        (method.upper(), path.format_map(ids))
        for path, operations in paths.items()
        if path.startswith("/admin")
        for method in operations
    ]


@pytest.fixture
def ids(db: Session) -> dict[str, str]:
    """Ids of a real problem and a real contest it belongs to, for every path parameter."""
    problem = Problem(slug="sum", title="Sum", is_public=True)
    contest = add_contest(db, starts_in=timedelta(days=1))
    db.add(problem)
    db.flush()
    db.add(ContestProblem(contest_id=contest.id, label="A", problem_id=problem.id))
    db.commit()
    return {"problem_id": str(problem.id), "contest_id": str(contest.id), "label": "A"}


def test_admin_routes_exist(settings: Settings, ids: dict[str, str]) -> None:
    endpoints = admin_endpoints(settings, ids)
    assert ("GET", "/admin/problems") in endpoints
    assert ("POST", f"/admin/problems/{ids['problem_id']}/tests") in endpoints
    assert ("POST", f"/admin/contests/{ids['contest_id']}/problems/A/remove") in endpoints


def test_anonymous_visitor_is_refused_every_admin_route(
    client: TestClient, settings: Settings, ids: dict[str, str]
) -> None:
    for method, path in admin_endpoints(settings, ids):
        response = client.request(method, path)
        assert response.status_code == 403, f"{method} {path}"
        assert "Only administrators" in response.text


def test_logged_in_non_admin_is_refused_every_admin_route(
    client: TestClient, settings: Settings, ids: dict[str, str]
) -> None:
    register(client, "alice", "correct horse battery")
    # A valid CSRF token, so the refusal can only come from the admin check.
    csrf_token = csrf_token_from(client.get("/"))
    for method, path in admin_endpoints(settings, ids):
        response = client.request(method, path, data={"csrf_token": csrf_token})
        assert response.status_code == 403, f"{method} {path}"
        assert "Only administrators" in response.text


def test_non_admin_sees_no_admin_link(client: TestClient) -> None:
    register(client, "alice", "correct horse battery")
    assert 'href="/admin/problems"' not in client.get("/").text
