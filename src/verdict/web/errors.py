import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException

from verdict.web.csrf import CsrfError
from verdict.web.deps import AdminOnlyError, Viewer, get_viewer
from verdict.web.templating import render

logger = logging.getLogger(__name__)

MESSAGES = {
    403: "Only administrators can open this page.",
    404: "There is nothing at this address, or you don't have access to it.",
}


def install_error_pages(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> HTMLResponse:
        message = MESSAGES.get(error.status_code, str(error.detail))
        return await error_page(request, error.status_code, message)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> HTMLResponse:
        return await error_page(request, 400, "The address or form was not valid.")

    @app.exception_handler(AdminOnlyError)
    async def admin_only(request: Request, error: AdminOnlyError) -> HTMLResponse:
        return await error_page(request, 403, MESSAGES[403])

    @app.exception_handler(CsrfError)
    async def csrf_failed(request: Request, error: CsrfError) -> HTMLResponse:
        logger.warning("csrf check failed", extra={"path": request.url.path})
        return await error_page(
            request,
            403,
            "This form has expired or came from another site. "
            "Go back, reload the page and try again.",
            title="Form expired",
        )


async def error_page(
    request: Request, status_code: int, message: str, title: str | None = None
) -> HTMLResponse:
    # Exception handlers run outside the route, so the viewer is loaded here for the page header.
    viewer = await run_in_threadpool(_load_viewer, request)
    context = {"title": title or HTTPStatus(status_code).phrase, "message": message}
    return render(request, viewer, "error.html", context, status_code=status_code)


def _load_viewer(request: Request) -> Viewer:
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    with session_factory() as db:
        return get_viewer(request, request.app.state.redis, db)
