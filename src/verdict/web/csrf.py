import hmac
from typing import Annotated

from fastapi import Depends, Form

from verdict.web.deps import ViewerDep


class CsrfError(Exception):
    def __init__(self) -> None:
        super().__init__("form token missing or does not match the session")


def require_csrf(viewer: ViewerDep, csrf_token: Annotated[str, Form()] = "") -> None:
    """Reject a form post unless it carries the token issued with this browser's session."""
    session = viewer.session
    if session is None or not hmac.compare_digest(csrf_token.encode(), session.csrf_token.encode()):
        raise CsrfError


CSRF_PROTECTED = [Depends(require_csrf)]
