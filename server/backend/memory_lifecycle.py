"""Validated batch soft deletion, recovery and durable operation receipts."""

import hashlib
import json
import uuid

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .auth import control_for
from .errors import ApiError
from .memory_models import TAB_TYPES, MemoryTab
from .memory_upstream import bad_upstream
from .operation_store import OperationStore
from .security import check_origin

router = APIRouter(prefix="/api/dashboard/project/memory")


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projectId: str = Field(min_length=1, max_length=128)
    memType: MemoryTab
    memory_ids: list[str] = Field(min_length=1, max_length=100)
    operationId: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )

    @field_validator("memory_ids")
    @classmethod
    def validate_ids(cls, values):
        if len(set(values)) != len(values) or any(
            not value or len(value) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in value)
            for value in values
        ):
            raise ValueError("Invalid or duplicate memory IDs")
        return values


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projectId: str = Field(min_length=1, max_length=128)
    operationId: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")


def dependencies(request, project):
    control = control_for(request)
    cube = control.resolve_cube(project) if control else None
    if cube is None:
        raise ApiError(40001, "项目不存在或未配置访问范围", status_code=404)
    if request.scope.get("identity"):
        control.require_role(project, "viewer" if request.method == "GET" else "editor")
    request.scope["audit_cube"] = cube
    return cube, OperationStore(control), request.app.state.memories


async def upstream_call(upstream, path, body):
    data = await upstream.request("POST", "product/" + path, body=body, allow_failure=True)
    if isinstance(data, dict) and "error_code" in data:
        code = data["error_code"]
        raise ApiError(
            {400: 40000, 403: 40003, 404: 40001, 409: 40900}[code],
            "上游拒绝该生命周期操作",
            status_code=code,
        )
    return data


def view(record):
    confirmed = sum(item["state"] == "confirmed" for item in record["results"])
    return {
        "operationId": record["operationId"],
        "state": "confirmed" if confirmed == len(record["results"]) else "pending",
        "deleted" if record["action"] == "deleted" else "restored": confirmed,
        "results": record["results"],
    }


def answer(record, request=None):
    result = view(record)
    success = result["state"] == "confirmed"
    if request is not None:
        request.scope["audit_code"] = 0 if success else 50301
    return JSONResponse(
        status_code=200 if success else 503,
        content={
            "code": 0 if success else 50301,
            "data": result,
            "message": "success"
            if success
            else "部分记忆尚未确认完成，请使用 operationId 查询或重试",
        },
        headers={"Cache-Control": "no-store"},
    )


def validate_result(data, record, item):
    if (
        not isinstance(data, dict)
        or data.get("operation_id") != item["operationId"]
        or data.get("memory_id") != item["memoryId"]
        or data.get("kind") != record["kind"]
        or data.get("target_status")
        != ("deleted" if record["action"] == "deleted" else "activated")
        or data.get("state") not in ("confirmed", "pending")
        or type(data.get("success")) is not bool
        or data["success"] != (data["state"] == "confirmed")
    ):
        raise bad_upstream()
    if data["success"] and data.get("vector_state") not in (
        ("not_applicable",)
        if record["kind"] == "profile"
        else ("confirmed", "explicitly_not_indexed")
    ):
        raise bad_upstream()
    return {
        "memoryId": item["memoryId"],
        "operationId": item["operationId"],
        "state": data["state"],
        "retryable": not data["success"],
        "vectorState": data.get("vector_state", "unconfirmed"),
    }


async def execute(record, cube, store, upstream):
    for item in record["results"]:
        if item["state"] == "confirmed":
            continue
        try:
            data = await upstream_call(
                upstream,
                "transition_profile_memory" if record["kind"] == "profile" else "transition_memory",
                {
                    "mem_cube_id": cube,
                    "memory_id": item["memoryId"],
                    "operation_id": item["operationId"],
                    "kind": record["kind"],
                    "target_status": record["action"],
                },
            )
            result = validate_result(data, record, item)
        except ApiError as exc:
            result = {**item, "state": "pending", "retryable": True, "errorCode": exc.code}
        record = store.merge_result(record, result)
    return record


async def start(body, request, action):
    check_origin(request)
    cube, store, upstream = dependencies(request, body.projectId)
    fingerprint = json.dumps([body.projectId, body.memType, action, sorted(body.memory_ids)])
    previous = store.get(body.operationId, body.projectId)
    if previous:
        if previous["fingerprint"] != fingerprint:
            raise ApiError(40900, "operationId 已用于其他操作", status_code=409)
        return answer(await execute(previous, cube, store, upstream), request)
    kind = "profile" if body.memType == "profile_mem" else "graph"
    for identifier in body.memory_ids:
        data = await upstream_call(
            upstream,
            "inspect_memory_lifecycle",
            {"mem_cube_id": cube, "memory_id": identifier, "kind": kind},
        )
        if not isinstance(data, dict) or data.get("memory_id") != identifier:
            raise bad_upstream()
        if data.get("memory_type") not in TAB_TYPES[body.memType]:
            raise ApiError(40000, "记忆类型与标签不匹配", status_code=400)
        if data.get("status") != ("activated" if action == "deleted" else "deleted") or (
            action == "restore" and data.get("previous_status") != "activated"
        ):
            raise ApiError(40900, "记忆状态不允许该操作", status_code=409)
    record = store.prepare(
        {
            "operationId": body.operationId,
            "projectId": body.projectId,
            "fingerprint": fingerprint,
            "kind": kind,
            "action": action,
            "results": [
                {
                    "memoryId": identifier,
                    "operationId": "dash-"
                    + hashlib.sha256(
                        json.dumps([body.operationId, identifier]).encode()
                    ).hexdigest(),
                    "state": "queued",
                    "retryable": True,
                }
                for identifier in body.memory_ids
            ],
        }
    )
    return answer(await execute(record, cube, store, upstream), request)


@router.post("/delete")
async def delete(body: BatchRequest, request: Request):
    return await start(body, request, "deleted")


@router.post("/restore")
async def restore(body: BatchRequest, request: Request):
    return await start(body, request, "restore")


@router.post("/retry")
async def retry(body: OperationRequest, request: Request):
    check_origin(request)
    cube, store, upstream = dependencies(request, body.projectId)
    record = store.get(body.operationId, body.projectId)
    if record is None:
        raise ApiError(40001, "操作不存在", status_code=404)
    return answer(await execute(record, cube, store, upstream), request)


@router.get("/operation")
async def operation(
    request: Request,
    response: Response,
    projectId: str = Query(..., min_length=1, max_length=128),
    operationId: str = Query(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$"),
):
    validated = OperationRequest(projectId=projectId, operationId=operationId)
    cube, store, upstream = dependencies(request, validated.projectId)
    record = store.get(validated.operationId, validated.projectId)
    if record is None:
        raise ApiError(40001, "操作不存在", status_code=404)
    for item in record["results"]:
        if item["state"] == "confirmed":
            continue
        try:
            data = await upstream_call(
                upstream,
                "get_memory_operation",
                {"mem_cube_id": cube, "operation_id": item["operationId"]},
            )
            if data is not None:
                record = store.merge_result(record, validate_result(data, record, item))
        except ApiError:
            pass  # Keep the persisted pending outcome; never fabricate confirmation.
    response.headers["Cache-Control"] = "no-store"
    return {"code": 0, "data": view(record), "message": "success"}
