from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from verdict.web.deps import Viewer
from verdict.web.sessions import set_session_cookie

TEMPLATES_DIR = Path(__file__).parent / "templates"


def format_seconds(milliseconds: int) -> str:
    return f"{milliseconds / 1000:g} s"


# Starlette's default only escapes files ending in .html; this escapes every template.
templates = Jinja2Templates(
    env=Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)
)
templates.env.filters["seconds"] = format_seconds


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
