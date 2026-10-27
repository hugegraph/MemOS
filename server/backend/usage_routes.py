"""Daily usage reuses the same imported logical calls as the log APIs."""

import sqlite3

from fastapi import APIRouter, Request, Response

from .audit_index import unavailable
from .auth import control_for
from .errors import ApiError
from .models import Envelope
from .usage_models import UsageRequest

router = APIRouter()


@router.post("/api/dashboard/usage/query", response_model=Envelope[dict])
def usage_query(body: UsageRequest, request: Request, response: Response):
    control = control_for(request)
    if body.projectId not in (None, "all"):
        cube = control.resolve_cube(body.projectId) if control else None
        if cube is None:
            raise ApiError(40001, "项目不存在或未配置访问范围", status_code=404)
        cubes = [cube]
    else:
        cubes = control.authorized_cubes() if control else []
        if not cubes:
            raise ApiError(50300, "尚未配置可访问项目范围", status_code=503)
    if body.apiKeyId:
        raise ApiError(
            40000, "当前审计来源不含可信密钥标识，暂不支持 apiKeyId 筛选", status_code=400
        )
    # Authorization and unsupported filters are resolved before reading any audit source.
    index = request.app.state.audit.sync("gateway")
    try:
        result = index.usage(cubes, *body.bounds(), include_daily=body.includeDailyDetails)
    except (OSError, sqlite3.Error) as exc:
        raise unavailable() from exc
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data=result)
