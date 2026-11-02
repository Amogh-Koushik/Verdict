import re

import httpx2
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from verdict.core.accounts import create_user
from verdict.web.sessions import SESSION_COOKIE

ADMIN_PASSWORD = "admin password 1"

CSRF_FIELD = re.compile(r'name="csrf_token" value="([^"]+)"')


def csrf_token_from(page: httpx2.Response) -> str:
    match = CSRF_FIELD.search(page.text)
    assert match, "page has no CSRF field"
    return match.group(1)


def session_token(client: TestClient) -> str | None:
    return client.cookies.get(SESSION_COOKIE)


def register(
    client: TestClient, username: str, password: str, confirm: str | None = None
) -> httpx2.Response:
    csrf_token = csrf_token_from(client.get("/register"))
    return client.post(
        "/register",
        data={
            "csrf_token": csrf_token,
            "username": username,
            "password": password,
            "password_confirm": password if confirm is None else confirm,
        },
        follow_redirects=False,
    )


def log_in(client: TestClient, username: str, password: str) -> httpx2.Response:
    csrf_token = csrf_token_from(client.get("/login"))
    return client.post(
        "/login",
        data={"csrf_token": csrf_token, "username": username, "password": password},
        follow_redirects=False,
    )


def log_out(client: TestClient) -> httpx2.Response:
    csrf_token = csrf_token_from(client.get("/"))
    return client.post("/logout", data={"csrf_token": csrf_token}, follow_redirects=False)


def log_in_as_admin(client: TestClient, db: Session, username: str = "admin") -> None:
    create_user(db, username, ADMIN_PASSWORD, is_admin=True)
    response = log_in(client, username, ADMIN_PASSWORD)
    assert response.status_code == 303
