"""Normalize only evidence present in audit lines; never guess tenant/subjects."""

import json
import math
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

TOOL_EVENTS = {
    "add_message": "addMessage",
    "search_memory": "searchMemory",
    "get_user_profile": "getMemory",
    "delete_memory": "deleteMemory",
    "add_feedback": "addFeedback",
}
SECRET_KEY = re.compile(r"(?:apikey|authorization|password|secret|accesstoken|refreshtoken|token)$")
SECRET_VALUE = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
BODY_LIMIT = 65536


class UnscopedLine(ValueError):
    pass


@lru_cache(maxsize=512)
def credential_key(key: str) -> bool:
    """Cache common schema keys, never credential values or unbounded long keys."""
    return bool(SECRET_KEY.search(re.sub(r"[^a-z0-9]", "", key.casefold())))


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if (
                credential_key(key)
                if len(key) <= 128
                else SECRET_KEY.search(re.sub(r"[^a-z0-9]", "", key.casefold()))
            )
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return SECRET_VALUE.sub("[REDACTED]", value)
    return value


def decoded(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, RecursionError):
            return value
    return value


def body_json(value: Any) -> str:
    parsed = decoded(value)
    clean = redact(parsed)
    if isinstance(value, str) and clean == parsed and not isinstance(parsed, str):
        result = value
    elif isinstance(value, str) and clean == parsed:
        try:
            json.loads(value)
            result = value
        except ValueError:
            result = json.dumps(clean, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    else:
        result = json.dumps(clean, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(result) > BODY_LIMIT:
        result = json.dumps(
            {"truncated": True, "chars": len(result), "text": result[:BODY_LIMIT]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return result


def text(value: Any, limit: int = 256) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def integer(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def timestamp(value: Any) -> tuple[str, int]:
    try:
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.utcoffset() is None:
                return "", 0
        elif type(value) in (int, float) and math.isfinite(value):
            parsed = datetime.fromtimestamp(value, timezone.utc)
        else:
            return "", 0
        parsed = parsed.astimezone(timezone.utc)
        return parsed.isoformat(), round(parsed.timestamp() * 1_000_000)
    except (ValueError, OverflowError, OSError):
        return "", 0


def identifiers(entry: dict, request: dict, kind: str) -> list[str]:
    values = []
    for container in (entry, request):
        for key in (kind + "Ids", kind + "_ids", kind + "Id", kind + "_id"):
            value = container.get(key)
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, list):
                values.extend(item for item in value if isinstance(item, str))
    return sorted({item[:256] for item in values if item})[:100]


def normalize(entry: dict, source: str) -> dict:
    if not isinstance(entry, dict):
        raise ValueError("Audit line must be an object")
    raw_cube = entry.get("tenant") or entry.get("mem_cube_id") or entry.get("cube_id")
    if isinstance(raw_cube, str) and len(raw_cube) > 128:
        raise ValueError("Tenant identifier is too long")
    cube = text(raw_cube, 128)
    if cube is None:
        raise UnscopedLine("Audit tenant is absent")
    request = decoded(entry.get("input", entry.get("requestBody")))
    output = decoded(entry.get("output", entry.get("responseBody")))
    input_dict = request if isinstance(request, dict) else {}
    output_dict = output if isinstance(output, dict) else {}
    data = output_dict.get("data")
    data = data if isinstance(data, dict) else {}
    event = text(entry.get("eventType") or entry.get("event_type") or entry.get("tool"))
    event = TOOL_EVENTS.get(event, event)
    event = "extractMemory" if event == "extractMemoryDirect" else event
    raw_call_id = entry.get("callId") or entry.get("call_id")
    if isinstance(raw_call_id, str) and len(raw_call_id) > 256:
        raise ValueError("Call identifier is too long")
    call_id = text(raw_call_id)
    raw_phase = str(entry.get("phase") or "").lower()
    phase = (
        "terminal"
        if raw_phase in {"completed", "complete", "completion", "final", "terminal", "failed"}
        else "request"
        if raw_phase in {"request", "start", "accepted", "pending"}
        else "sync"
    )
    statuses = {
        str(value).lower()
        for value in (entry.get("status"), data.get("status"), output_dict.get("status"))
        if value is not None
    }
    codes = [
        integer(value)
        for value in (
            entry.get("responseCode"),
            entry.get("response_code"),
            output_dict.get("code"),
        )
    ]
    response_code = integer(
        entry.get("responseCode", entry.get("response_code", output_dict.get("code")))
    )
    failed = (
        entry.get("ok") is False
        or entry.get("err") not in (None, "", False)
        or output_dict.get("success") is False
        or data.get("success") is False
        or bool(statuses & {"failed", "failure", "error"})
        or raw_phase == "failed"
        or any(value is not None and value not in (0, 200) for value in codes)
    )
    pending = bool(statuses & {"pending", "running", "in_progress", "queued"}) or (
        phase == "request" and bool(call_id)
    )
    status = (
        "FAILURE"
        if failed
        else "PENDING"
        if pending
        else "SUCCESS"
        if entry.get("ok") is True
        or bool(statuses & {"success", "succeeded", "completed", "done"})
        or any(value in (0, 200) for value in codes)
        else "UNKNOWN"
    )
    if call_id and status == "PENDING":
        phase = "request"
    elif call_id and phase == "sync" and status in {"SUCCESS", "FAILURE"}:
        phase = "terminal"
    duration = entry.get("ms", entry.get("duration"))
    duration = (
        duration
        if type(duration) in (int, float)
        and math.isfinite(duration)
        and duration >= 0
        and status != "PENDING"
        else None
    )
    usage = output_dict.get("usage") or data.get("usage") or entry.get("usage") or {}
    usage = usage if isinstance(usage, dict) else {}
    request_tokens = integer(
        entry.get("requestTokens", usage.get("prompt_tokens", usage.get("input_tokens")))
    )
    response_tokens = integer(
        entry.get("responseTokens", usage.get("completion_tokens", usage.get("output_tokens")))
    )
    observed = [
        key
        for key, value in (
            ("duration", duration),
            ("request_tokens", request_tokens),
            ("response_tokens", response_tokens),
            ("request_body", entry.get("input", entry.get("requestBody"))),
            ("response_body", entry.get("output", entry.get("responseBody"))),
        )
        if value is not None
    ]
    time, ts = timestamp(entry.get("ts", entry.get("createTime")))
    return {
        "cube": cube,
        "source": source,
        "event": event,
        "call_id": call_id,
        "phase": phase,
        "status": status,
        "duration": duration,
        "time": time,
        "ts": ts,
        "users": identifiers(entry, input_dict, "user"),
        "agents": identifiers(entry, input_dict, "agent"),
        "request_tokens": request_tokens or 0,
        "response_tokens": response_tokens or 0,
        "observed": observed,
        "request_body": body_json(entry.get("input", entry.get("requestBody"))),
        "response_body": body_json(entry.get("output", entry.get("responseBody"))),
        "trace_id": text(entry.get("traceId") or entry.get("trace_id")),
        "request_id": text(entry.get("requestId") or entry.get("request_id")),
        "request_code": integer(entry.get("requestCode", entry.get("request_code"))),
        "response_code": response_code,
        "llm_calls": integer(entry.get("llmCalls", data.get("llm_calls"))),
    }
