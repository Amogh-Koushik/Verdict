from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from verdict.web.deps import ViewerDep
from verdict.web.templating import render

router = APIRouter()


@router.get("/")
def home(request: Request, viewer: ViewerDep) -> HTMLResponse:
    return render(request, viewer, "home.html")
