"""Six read-only views: explicit tenancy, native pagination, whitelist projection."""

from datetime import datetime

from fastapi import APIRouter, Request, Response

from .auth import control_for
from .errors import ApiError
from .memory_models import TAB_TYPES, MemoryDetailRequest, MemoryItem, MemoryListRequest, MemoryPage
from .memory_upstream import bad_upstream
from .models import Envelope
from .project_context import selected_project
from .timefmt import display


def project_node(node: dict, cube: str, tab: str, *, detail: bool = False) -> MemoryItem:
    profile = tab == "profile_mem"
    metadata = node if profile else node.get("metadata")
    if not isinstance(metadata, dict):
        raise bad_upstream()
    if metadata.get("user_name") not in (None, cube):
        raise bad_upstream()
    kind = metadata.get("memory_type")
    if kind not in TAB_TYPES[tab] or metadata.get("status") != "activated":
        raise bad_upstream()
    identifier, text, created = node.get("id"), node.get("memory"), metadata.get("created_at")
    if (
        not isinstance(identifier, str)
        or not identifier
        or not isinstance(text, str)
        or not isinstance(created, str)
    ):
        raise bad_upstream()
    try:
        timestamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if timestamp.utcoffset() is None:
            raise ValueError("Missing timezone")
    except ValueError as exc:
        raise bad_upstream() from exc
    values = {
        "memoryId": identifier,
        "memoryContent": text if detail or len(text) <= 500 else text[:500] + "…",
        "createTime": display(timestamp.isoformat()),
        "memoryType": kind,
    }
    owner = metadata.get("subject_type" if profile else "owner_type")
    if owner is not None and not isinstance(owner, str):
        raise bad_upstream()
    subject_id = metadata.get("subject_id") if profile else None
    if owner is None and not profile:
        if metadata.get("user_id"):
            owner, subject_id = "user", metadata["user_id"]
        elif metadata.get("agent_id"):
            owner, subject_id = "agent", metadata["agent_id"]
    if owner in {"user", "agent", "public"}:
        values["subjectType"] = owner
        if not profile:
            subject_id = (
                ""
                if owner == "public"
                else metadata.get("owner_id")
                or metadata.get("user_id" if owner == "user" else "agent_id")
            )
        if isinstance(subject_id, str):
            values["externalUserId"] = subject_id
    if tab == "skill_mem":
        for out, source in [("skillName", "name"), ("skillUrl", "url")]:
            if isinstance(metadata.get(source), str):
                values[out] = metadata[source]
    if profile:
        for out, source in [
            ("profileTemplateId", "template_id"),
            ("profileInstanceId", "profile_instance_id"),
        ]:
            if isinstance(metadata.get(source), str) and metadata[source]:
                values[out] = metadata[source]
    return MemoryItem(**values)


router = APIRouter()


@router.post(
    "/api/dashboard/project/memory/list",
    response_model=Envelope[MemoryPage],
    response_model_exclude_none=True,
)
async def list_memories(body: MemoryListRequest, request: Request, response: Response):
    control = control_for(request)
    cube = control.resolve_cube(body.projectId) if control else None
    if cube is None:
        raise ApiError(40001, "项目不存在或未配置访问范围", status_code=404)
    request.scope["audit_cube"] = cube
    rows, total = await request.app.state.memories.page(cube, body)
    result = MemoryPage(
        memListItemResponseList=[project_node(row, cube, body.memoryDetailType) for row in rows],
        total=total,
        current=body.pageNum,
    )
    if len(result.model_dump_json().encode("utf-8")) > 200 * 1024:
        raise bad_upstream()
    response.headers["Cache-Control"] = "no-store"
    request.scope["audit_code"] = 0
    return Envelope(data=result)


@router.post(
    "/api/dashboard/project/memory/detail",
    response_model=Envelope[MemoryItem],
    response_model_exclude_none=True,
)
async def memory_detail(body: MemoryDetailRequest, request: Request, response: Response):
    control = control_for(request)
    cube = control.resolve_cube(selected_project(request)) if control else None
    if cube is None:
        raise ApiError(50300, "尚未配置当前项目 DASHBOARD_DEFAULT_PROJECT_ID", status_code=503)
    request.scope["audit_cube"] = cube
    row = await request.app.state.memories.detail(cube, body.memoryId, body.memType)
    metadata = (row if body.memType == "profile_mem" else row.get("metadata", {})) if row else {}
    if not isinstance(metadata, dict):
        raise bad_upstream()
    if row is None or metadata.get("status") != "activated":
        raise ApiError(40001, "记忆不存在", status_code=404)
    if metadata.get("memory_type") not in TAB_TYPES[body.memType]:
        raise ApiError(40000, "记忆类型与标签不匹配", status_code=400)
    result = project_node(row, cube, body.memType, detail=True)
    response.headers["Cache-Control"] = "no-store"
    request.scope["audit_code"] = 0
    return Envelope(data=result)
