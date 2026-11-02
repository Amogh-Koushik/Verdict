from fastapi.testclient import TestClient

from tests.integration.forms import csrf_token_from, register
from verdict.core.config import Settings
from verdict.web.main import create_app

PASSWORD = "correct horse battery"


def test_every_post_route_refuses_a_post_without_the_csrf_token(
    client: TestClient, settings: Settings
) -> None:
    paths = create_app(settings).openapi()["paths"]
    post_paths = [path for path, operations in paths.items() if "post" in operations]
    assert {"/login", "/register", "/logout"} <= set(post_paths)
    client.get("/login")
    for path in post_paths:
        response = client.post(path, data={"username": "alice", "password": PASSWORD})
        assert response.status_code == 403, f"{path} accepted a post without a CSRF token"
        assert "Form expired" in response.text


def test_post_with_another_sessions_token_is_refused(
    client: TestClient, settings: Settings
) -> None:
    with TestClient(create_app(settings)) as other_browser:
        foreign_token = csrf_token_from(other_browser.get("/login"))
    client.get("/login")
    response = client.post(
        "/login", data={"csrf_token": foreign_token, "username": "alice", "password": PASSWORD}
    )
    assert response.status_code == 403


def test_post_without_any_session_is_refused(client: TestClient) -> None:
    response = client.post(
        "/login", data={"csrf_token": "made-up", "username": "alice", "password": PASSWORD}
    )
    assert response.status_code == 403


def test_refused_login_does_not_log_the_user_in(client: TestClient) -> None:
    register(client, "alice", PASSWORD)
    client.cookies.clear()
    client.get("/login")
    client.post("/login", data={"csrf_token": "wrong", "username": "alice", "password": PASSWORD})
    assert "You are logged in" not in client.get("/").text
