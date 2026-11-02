import re

import httpx2
from fastapi.testclient import TestClient

from verdict.web.sessions import SESSION_COOKIE

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
