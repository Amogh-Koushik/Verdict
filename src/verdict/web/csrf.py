import hmac
import logging
from typing import Annotated

from fastapi import Depends, Form, Request
from fastapi.responses import HTMLResponse

from verdict.web.deps import Viewer, ViewerDep
from verdict.web.templating import render

logger = logging.getLogger(__name__)


class CsrfError(Exception):
    def __init__(self, viewer: Viewer) -> None:
        super().__init__("form token missing or does not match the session")
        self.viewer = viewer


def require_csrf(viewer: ViewerDep, csrf_token: Annotated[str, Form()] = "") -> None:
    """Reject a form post unless it carries the token issued with this browser's session."""
    session = viewer.session
    if session is None or not hmac.compare_digest(csrf_token.encode(), session.csrf_token.encode()):
        raise CsrfError(viewer)


CSRF_PROTECTED = [Depends(require_csrf)]


def csrf_rejection(request: Request, error: CsrfError) -> HTMLResponse:
    logger.warning("csrf check failed", extra={"path": request.url.path})
    return render(
        request,
        error.viewer,
        "error.html",
        {
            "title": "Form expired",
            "message": "This form has expired or came from another site. "
            "Go back, reload the page and try again.",
        },
        status_code=403,
    )
