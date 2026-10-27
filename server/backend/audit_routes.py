"""Audit queries run in worker threads: importing JSONL cannot block the event loop."""

import sqlite3
import threading

from fastapi import APIRouter, Request, Response

from .audit_index import AuditIndex, unavailable
from .audit_models import LogDetailRequest, LogListRequest
from .auth import control_for
from .errors import ApiError
from .models import Envelope
from .project_context import selected_project

router = APIRouter()


class AuditService:
    """Lazily initialize so the first-query benchmark includes database creation."""

    def __init__(self, config):
        self.config, self.index = config, None
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            if self.index is None:
                sources = {
                    "console": self.config.audit_console_dir or self.config.data_dir / "audit"
                }
                if self.config.audit_gateway_dir is not None:
                    sources["gateway"] = self.config.audit_gateway_dir
                self.index = AuditIndex(self.config.data_dir / "audit.db", sources)
            return self.index

    def sync(self, scope="all"):
        if scope in {"all", "gateway"} and self.config.audit_gateway_dir is None:
            raise unavailable()
        try:
            index = self.get()
            index.sync(scope)
            return index
        except (OSError, sqlite3.Error) as exc:
            raise unavailable() from exc


@router.post("/api/dashboard/log/list", response_model=Envelope[dict])
def log_list(body: LogListRequest, request: Request, response: Response):
    control = control_for(request)
    cube = control.resolve_cube(body.projectId) if control else None
    if cube is None:
        raise ApiError(40001, "项目不存在或未配置访问范围", status_code=404)
    index = request.app.state.audit.sync(body.scope)
    try:
        result = index.page(cube, body)
    except (OSError, sqlite3.Error) as exc:
        raise unavailable() from exc
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data=result)


@router.post("/api/dashboard/log/detail", response_model=Envelope[dict])
def log_detail(body: LogDetailRequest, request: Request, response: Response):
    control = control_for(request)
    cube = control.resolve_cube(selected_project(request)) if control else None
    if cube is None:
        raise ApiError(50300, "尚未配置当前项目 DASHBOARD_DEFAULT_PROJECT_ID", status_code=503)
    # An indexed console detail does not depend on gateway availability.
    try:
        index = request.app.state.audit.get()
        detail = index.detail(cube, body.logId)
    except (OSError, sqlite3.Error) as exc:
        raise unavailable() from exc
    if detail is None:
        raise ApiError(40001, "日志不存在", status_code=404)
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data=detail)
