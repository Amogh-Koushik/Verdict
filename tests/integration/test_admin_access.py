import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.integration.forms import csrf_token_from, register
from verdict.core.config import Settings
from verdict.core.models import Problem
from verdict.web.main import create_app


def admin_endpoints(settings: Settings, problem_id: int) -> list[tuple[str, str]]:
    """Every (method, path) under /admin, with a real problem id filled in."""
    paths = create_app(settings).openapi()["paths"]
    return [
        (method.upper(), path.replace("{problem_id}", str(problem_id)))
        for path, operations in paths.items()
        if path.startswith("/admin")
        for method in operations
    ]


@pytest.fixture
def problem_id(db: Session) -> int:
    problem = Problem(slug="sum", title="Sum", is_public=True)
    db.add(problem)
    db.commit()
    return problem.id


def test_admin_routes_exist(settings: Settings, problem_id: int) -> None:
    endpoints = admin_endpoints(settings, problem_id)
    assert ("GET", "/admin/problems") in endpoints
    assert ("POST", f"/admin/problems/{problem_id}/tests") in endpoints


def test_anonymous_visitor_is_refused_every_admin_route(
    client: TestClient, settings: Settings, problem_id: int
) -> None:
    for method, path in admin_endpoints(settings, problem_id):
        response = client.request(method, path)
        assert response.status_code == 403, f"{method} {path}"
        assert "Only administrators" in response.text


def test_logged_in_non_admin_is_refused_every_admin_route(
    client: TestClient, settings: Settings, problem_id: int
) -> None:
    register(client, "alice", "correct horse battery")
    # A valid CSRF token, so the refusal can only come from the admin check.
    csrf_token = csrf_token_from(client.get("/"))
    for method, path in admin_endpoints(settings, problem_id):
        response = client.request(method, path, data={"csrf_token": csrf_token})
        assert response.status_code == 403, f"{method} {path}"
        assert "Only administrators" in response.text


def test_non_admin_sees_no_admin_link(client: TestClient) -> None:
    register(client, "alice", "correct horse battery")
    assert 'href="/admin/problems"' not in client.get("/").text
