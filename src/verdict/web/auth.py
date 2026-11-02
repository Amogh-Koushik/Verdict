import logging
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response
from redis import Redis

from verdict.core.accounts import AccountError, UsernameTakenError, authenticate, create_user
from verdict.core.config import Settings
from verdict.core.models import User
from verdict.web.csrf import CSRF_PROTECTED
from verdict.web.deps import DbDep, FormViewerDep, RedisDep, SettingsDep, Viewer, ViewerDep
from verdict.web.rate_limit import RateLimitExceededError, hit_fixed_window
from verdict.web.sessions import (
    clear_session_cookie,
    create_session,
    delete_session,
    set_session_cookie,
)
from verdict.web.templating import render

logger = logging.getLogger(__name__)

router = APIRouter()

LOGIN_ATTEMPTS_PER_WINDOW = 5
LOGIN_WINDOW_SECONDS = 60

FormField = Annotated[str, Form()]


@router.get("/register")
def register_form(request: Request, viewer: FormViewerDep) -> Response:
    if viewer.user is not None:
        return RedirectResponse("/", status_code=303)
    return render(request, viewer, "register.html")


@router.post("/register", dependencies=CSRF_PROTECTED)
def register(
    request: Request,
    viewer: ViewerDep,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    username: FormField = "",
    password: FormField = "",
    password_confirm: FormField = "",
) -> Response:
    def form_error(message: str, status_code: int) -> Response:
        context = {"error": message, "username": username}
        return render(request, viewer, "register.html", context, status_code=status_code)

    if password != password_confirm:
        return form_error("The two passwords don't match.", 400)
    try:
        user = create_user(db, username, password)
    except UsernameTakenError as error:
        return form_error(str(error), 409)
    except AccountError as error:
        return form_error(str(error), 400)
    logger.info("user registered", extra={"user_id": user.id})
    return _start_user_session(redis, settings, viewer, user)


@router.get("/login")
def login_form(request: Request, viewer: FormViewerDep) -> Response:
    if viewer.user is not None:
        return RedirectResponse("/", status_code=303)
    return render(request, viewer, "login.html")


@router.post("/login", dependencies=CSRF_PROTECTED)
def login(
    request: Request,
    viewer: ViewerDep,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    username: FormField = "",
    password: FormField = "",
) -> Response:
    client_ip = request.client.host if request.client else "unknown"

    def form_error(
        message: str, status_code: int, headers: dict[str, str] | None = None
    ) -> Response:
        context = {"error": message, "username": username}
        return render(
            request, viewer, "login.html", context, status_code=status_code, headers=headers
        )

    # Keyed on the pair, not the IP alone: a college lab shares one public IP.
    rate_key = f"login:{client_ip}:{username.lower()[:64]}"
    try:
        hit_fixed_window(redis, rate_key, LOGIN_ATTEMPTS_PER_WINDOW, LOGIN_WINDOW_SECONDS)
    except RateLimitExceededError as error:
        logger.warning("login rate limited", extra={"client_ip": client_ip, "username": username})
        wait = error.retry_after_seconds
        return form_error(
            f"Too many login attempts. Try again in {wait} seconds.",
            429,
            headers={"Retry-After": str(wait)},
        )

    user = authenticate(db, username, password)
    if user is None:
        logger.info("login failed", extra={"client_ip": client_ip, "username": username})
        return form_error("Wrong username or password.", 401)
    logger.info("user logged in", extra={"user_id": user.id})
    return _start_user_session(redis, settings, viewer, user)


@router.post("/logout", dependencies=CSRF_PROTECTED)
def logout(viewer: ViewerDep, redis: RedisDep, settings: SettingsDep) -> Response:
    if viewer.session is not None:
        delete_session(redis, viewer.session.token)
    response = RedirectResponse("/", status_code=303)
    clear_session_cookie(response, settings)
    return response


def _start_user_session(redis: Redis, settings: Settings, viewer: Viewer, user: User) -> Response:
    # A fresh token at login means a token planted before login (session fixation) is useless.
    if viewer.session is not None:
        delete_session(redis, viewer.session.token)
    session = create_session(redis, user.id, settings.session_ttl_seconds)
    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, session, settings)
    return response
