"""Bounded console audit capture. Log queries never generate business call rows."""

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from .audit_normalize import body_json
from .auth import control_for
from .project_context import selected_project

logger = logging.getLogger(__name__)
LIMIT = 65536
PREFIX = "/api/dashboard/project/memory/"
EVENTS = {
    "list": "console.memory.list",
    "detail": "getMemory",
    "delete": "deleteMemory",
    "restore": "console.memory.restore",
    "retry": "console.memory.retry",
}


class ConsoleWriter:
    def __init__(self, folder):
        self.folder, self.lock = folder, threading.Lock()
        folder.mkdir(parents=True, exist_ok=True)

    def append(self, record):
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        filename = "console-" + datetime.now(timezone.utc).strftime("%Y%m%d") + ".jsonl"
        # One append syscall; serialization protects simultaneous worker threads.
        with self.lock, (self.folder / filename).open("ab", buffering=0) as file:
            file.write(line.encode("utf-8"))


def captured_body(raw, size):
    if size > LIMIT:
        # Do not retain a sliced secret or invalid JSON from an oversized payload.
        return {"truncated": True, "bytes": size}
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        return {"invalidJson": True, "bytes": size}


class ConsoleAuditMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        event = EVENTS.get(path.removeprefix(PREFIX)) if path.startswith(PREFIX) else None
        if scope["type"] != "http" or scope.get("method") != "POST" or event is None:
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        request_parts, response_parts = bytearray(), bytearray()
        request_size = response_size = 0
        http_status = 500

        async def capture_receive():
            nonlocal request_size
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                request_size += len(body)
                request_parts.extend(body[: max(0, LIMIT - len(request_parts))])
            return message

        async def capture_send(message):
            nonlocal response_size, http_status
            if message["type"] == "http.response.start":
                http_status = message["status"]
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                response_size += len(body)
                response_parts.extend(body[: max(0, LIMIT - len(response_parts))])
            await send(message)

        await self.app(scope, capture_receive, capture_send)
        try:
            state = scope["app"].state
            input_body = captured_body(request_parts, request_size)
            output = captured_body(response_parts, response_size)
            control = control_for(Request(scope))
            # Only detail's contract lacks projectId. An invalid explicit project never falls back.
            project = (
                selected_project(Request(scope))
                if path == PREFIX + "detail"
                else input_body.get("projectId")
                if isinstance(input_body, dict)
                else None
            )
            cube = scope.get("audit_cube")
            if cube is None and control and project:
                cube = await run_in_threadpool(control.resolve_cube, project)
            if cube is not None:
                code = scope.get(
                    "audit_code", output.get("code") if isinstance(output, dict) else None
                )
                success = (
                    (http_status < 400 and code == 0)
                    if code is not None
                    else False
                    if http_status >= 400
                    else None
                )
                record = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "tenant": cube,
                    "eventType": event,
                    "callId": uuid.uuid4().hex,
                    "ok": success,
                    "ms": round((time.perf_counter() - started) * 1000, 3),
                    "responseCode": code,
                    "input": body_json(input_body),
                    "output": body_json(output),
                }
                await run_in_threadpool(state.console_audit.append, record)
        except Exception as exc:
            # Audit I/O failure cannot undo an already delivered mutation or leak its payload.
            logger.error("Console audit write failed (%s)", type(exc).__name__)
