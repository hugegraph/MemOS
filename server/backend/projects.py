"""Real SQLite project lifecycle, no upstream memory deletion or fake account identity."""

import sqlite3
from contextlib import contextmanager

from fastapi import APIRouter, Path, Request, Response

from .auth import control_for
from .errors import ApiError
from .memory_lifecycle import check_origin
from .models import Envelope
from .project_context import ensure_context
from .project_models import ProjectCreate, ProjectPageRequest, ProjectUpdate

router = APIRouter(prefix="/api/dashboard/project")


@contextmanager
def storage(request):
    try:
        yield control_for(request)
    except sqlite3.Error as exc:
        raise ApiError(50300, "项目存储暂不可用", status_code=503) from exc


@router.get("/dict", response_model=Envelope[list[dict]])
def dictionary(request: Request, response: Response, language: str | None = None):
    with storage(request) as store:
        key = ensure_context(request, response)
        response.headers["Cache-Control"] = "no-store"
        return Envelope(data=store.dictionary(context=key))


@router.post("/list", response_model=Envelope[dict])
def project_list(body: ProjectPageRequest, request: Request, response: Response):
    with storage(request) as store:
        key = ensure_context(request, response)
        response.headers["Cache-Control"] = "no-store"
        return Envelope(data=store.page(body, key))


@router.post("/create", response_model=Envelope[dict])
def create(body: ProjectCreate, request: Request, response: Response):
    check_origin(request)
    with storage(request) as store:
        key = ensure_context(request, response)
        result = store.create(body, key)
        response.headers["Cache-Control"] = "no-store"
        return Envelope(data=result)


@router.post("/update", response_model=Envelope[dict])
def update(body: ProjectUpdate, request: Request, response: Response):
    check_origin(request)
    with storage(request) as store:
        key = ensure_context(request, response)
        result = store.update(body, key)
        response.headers["Cache-Control"] = "no-store"
        return Envelope(data=result)


def transition(request, response, identifier, restore=False):
    check_origin(request)
    with storage(request) as store:
        key = ensure_context(request, response)
        result = store.archive(identifier, key, restore=restore)
        response.headers["Cache-Control"] = "no-store"
        return Envelope(
            data=result if restore else {"ok": True, "id": identifier, "archived": True}
        )


@router.post("/delete/{id}", response_model=Envelope[dict])
@router.post("/revoke/{id}", response_model=Envelope[dict])
def archive(
    request: Request,
    response: Response,
    id: str = Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$"),
):
    return transition(request, response, id)


@router.post("/restore/{id}", response_model=Envelope[dict])
def restore(
    request: Request,
    response: Response,
    id: str = Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$"),
):
    return transition(request, response, id, restore=True)
