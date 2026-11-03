from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from verdict.web.deps import Viewer
from verdict.web.sessions import set_session_cookie

TEMPLATES_DIR = Path(__file__).parent / "templates"
# India has no daylight saving time, so a fixed offset is exact and needs no time-zone database.
IST = timezone(timedelta(hours=5, minutes=30), "IST")


def format_seconds(milliseconds: int) -> str:
    return f"{milliseconds / 1000:g} s"


def format_ist(moment: datetime) -> str:
    return moment.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")


def format_clock(seconds: int) -> str:
    """A duration as hours:minutes:seconds, the way the page's countdown script shows it."""
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours}:{minute:02}:{second:02}"


# Starlette's default only escapes files ending in .html; this escapes every template.
templates = Jinja2Templates(
    env=Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)
)
templates.env.filters["seconds"] = format_seconds
templates.env.filters["ist"] = format_ist
templates.env.filters["clock"] = format_clock


def render(
    request: Request,
    viewer: Viewer,
    template: str,
    context: Mapping[str, Any] | None = None,
    *,
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
) -> HTMLResponse:
    page_context = {
        "current_user": viewer.user,
        "csrf_token": viewer.session.csrf_token if viewer.session else None,
        **(context or {}),
    }
    response = templates.TemplateResponse(
        request, template, page_context, status_code=status_code, headers=headers
    )
    if viewer.session_is_new and viewer.session is not None:
        set_session_cookie(response, viewer.session, request.app.state.settings)
    return response
