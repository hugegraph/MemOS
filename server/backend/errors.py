"""Keep every failure JSON-shaped without exposing arbitrary exception text."""

import logging
import sqlite3

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, code: int, message: str, *, status_code: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def error_response(code: int, message: str, status: int, headers=None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"code": code, "data": None, "message": message},
        headers={**(headers or {}), "Cache-Control": "no-store"},
    )


class ErrorBoundaryMiddleware:
    """Catch before ServerErrorMiddleware can log raw exception messages."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracked_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracked_send)
        except Exception as exc:
            logger.error("Unhandled backend error (%s)", type(exc).__name__)
            scope["audit_code"] = 50000
            if started:
                # Headers already sent cannot be replaced with a JSON envelope.
                raise
            await error_response(50000, "服务内部异常，请稍后重试", 500)(scope, receive, send)


def install_error_handlers(app: FastAPI) -> None:
    async def storage_error(request: Request, exc: sqlite3.Error) -> JSONResponse:
        return error_response(50300, "控制面存储暂不可用", 503)

    async def domain_error(request: Request, exc: ApiError) -> JSONResponse:
        request.scope["audit_code"] = exc.code
        return error_response(exc.code, exc.message, exc.status_code)

    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        request.scope["audit_code"] = 40000
        return error_response(40000, "请求参数不合法", 400)

    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code, message = {
            401: (40100, "请先登录"),
            403: (40003, "没有访问权限"),
            404: (40001, "接口或资源不存在"),
            405: (40000, "请求方法不支持"),
        }.get(
            exc.status_code,
            (50000, "服务内部异常") if exc.status_code >= 500 else (40000, "请求失败"),
        )
        request.scope["audit_code"] = code
        return error_response(code, message, exc.status_code, exc.headers)

    app.add_exception_handler(ApiError, domain_error)
    app.add_exception_handler(sqlite3.Error, storage_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(HTTPException, http_error)
    app.add_middleware(ErrorBoundaryMiddleware)
