"""Dashboard attribute templates backed exclusively by the upstream profile service."""

import json

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .auth import control_for
from .kb_models import Strict
from .kb_store import KnowledgeStore
from .models import Envelope
from .product_client import malformed
from .timefmt import display

router = APIRouter(prefix="/api/dashboard/profileTemplate")
RESERVED = {"projectId", "templateId", "templateName", "bindingCount", "updatedAt"}


def tree(value, *, upstream=False):
    """The dashboard contract is group -> field -> string/boolean leaf (two key levels)."""
    if not isinstance(value, dict) or not 1 <= len(value) <= 50:
        raise ValueError("Expected 1 to 50 groups")
    count = 0
    result = {}
    for group, fields in value.items():
        if group in RESERVED:
            raise ValueError("Reserved group name")
        valid_key(group)
        if not isinstance(fields, dict) or not fields:
            raise ValueError("Expected nonempty group")
        result[group] = {}
        for name, leaf in fields.items():
            valid_key(name)
            if name in {"value", "algorithm_updatable"}:
                raise ValueError("Ambiguous upstream field name")
            if not isinstance(leaf, dict) or set(leaf) - {"value", "algorithm_updatable"}:
                raise ValueError("Invalid field")
            text = leaf.get("value", "")
            automatic = leaf.get("algorithm_updatable", True)
            if upstream and text is None and type(automatic) is bool:
                # Native null means no default value. The dashboard's optional
                # string field is omitted, rather than inventing a literal value.
                result[group][name] = {"algorithm_updatable": automatic}
                count += 1
                continue
            if not isinstance(text, str) or len(text) > 4000 or type(automatic) is not bool:
                raise ValueError("Expected string value and boolean algorithm_updatable")
            result[group][name] = {"value": text, "algorithm_updatable": automatic}
            count += 1
    if count > 500 or len(json.dumps(result, ensure_ascii=False).encode()) > 128 * 1024:
        raise ValueError("Template exceeds size limit")
    return result


def valid_key(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 128
        or "." in value
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("Invalid template key")


class Project(Strict):
    projectId: str = Field(min_length=1, max_length=128)


class Page(Project):
    pageNum: int = Field(1, ge=1, le=2147483647)
    pageSize: int = Field(10, ge=1, le=200)


class Identifier(Project):
    templateId: str = Field(min_length=1, max_length=128)


class Template(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    projectId: str = Field(min_length=1, max_length=128)
    templateName: str = Field(min_length=1, max_length=128)

    @field_validator("templateName")
    @classmethod
    def name(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid template name")
        return value

    @model_validator(mode="after")
    def groups(self):
        self.__pydantic_extra__ = tree(self.model_extra)
        return self


class Update(Template):
    templateId: str = Field(min_length=1, max_length=128)


def scope(request, response, project, role="viewer"):
    cube = KnowledgeStore.project(control_for(request), project, role)
    response.headers["Cache-Control"] = "no-store"
    return cube, request.app.state.product


def view(row, project, expected=None):
    if not isinstance(row, dict):
        raise malformed()
    identifier = row.get("profile_template_id")
    name = row.get("name")
    count = row.get("bound_count")
    if (
        not isinstance(identifier, str)
        or not 1 <= len(identifier) <= 128
        or (expected and identifier != expected)
        or not isinstance(name, str)
        or not 1 <= len(name) <= 128
        or type(count) is not int
        or count < 0
    ):
        raise malformed()
    try:
        deprecated = row.get("deprecated_paths", [])
        if not isinstance(deprecated, list) or any(not isinstance(p, str) for p in deprecated):
            raise ValueError("Invalid deprecated paths")
        # Upstream retains removed fields for bound-instance history; the dashboard
        # template describes the active fields, not these retained historical values.
        source = row.get("metadata")
        if not isinstance(source, dict) or any(not isinstance(v, dict) for v in source.values()):
            raise ValueError("Invalid template metadata")
        active = {}
        for group, fields in source.items():
            active[group] = {
                key: leaf for key, leaf in fields.items() if group + "." + key not in deprecated
            }
            if not active[group]:
                del active[group]
        metadata = tree(active, upstream=True)
        updated = row.get("updated_at")
        if updated is not None and not isinstance(updated, str):
            raise ValueError("Invalid timestamp")
        updated = display(updated)
    except (ValueError, OverflowError) as exc:
        raise malformed() from exc
    result = {
        "templateId": identifier,
        "templateName": name,
        "projectId": project,
        "bindingCount": count,
        **metadata,
    }
    if updated is not None:
        result["updatedAt"] = updated
    return result


async def get(upstream, cube, project, identifier):
    data = await upstream.request(
        "get_profile_template",
        {
            "mem_cube_id": cube,
            "profile_template_id": identifier,
        },
    )
    return view(data, project, identifier)


@router.post("/list", response_model=Envelope[dict])
async def listing(body: Page, request: Request, response: Response):
    cube, upstream = scope(request, response, body.projectId)
    data = await upstream.request(
        "list_profile_template",
        {
            "mem_cube_id": cube,
            "page": body.pageNum,
            "page_size": body.pageSize,
        },
    )
    if not isinstance(data, dict):
        raise malformed()
    rows, total = data.get("profile_template_list"), data.get("total")
    if not isinstance(rows, list) or type(total) is not int or total < 0:
        raise malformed()
    expected = min(body.pageSize, max(0, total - (body.pageNum - 1) * body.pageSize))
    if len(rows) != expected:
        raise malformed()
    projected = [view(row, body.projectId) for row in rows]
    if len({row["templateId"] for row in projected}) != len(projected):
        raise malformed()
    return Envelope(
        data={
            "list": projected,
            "total": total,
            "current": body.pageNum,
            "size": body.pageSize,
            "pages": (total + body.pageSize - 1) // body.pageSize,
        }
    )


@router.get("/detail", response_model=Envelope[dict])
async def detail(
    request: Request,
    response: Response,
    projectId: str = Query(min_length=1, max_length=128),
    templateId: str = Query(min_length=1, max_length=128),
):
    cube, upstream = scope(request, response, projectId)
    return Envelope(data=await get(upstream, cube, projectId, templateId))


@router.post("/create", response_model=Envelope[dict])
async def create(body: Template, request: Request, response: Response):
    cube, upstream = scope(request, response, body.projectId, "owner")
    data = await upstream.request(
        "add_profile_template",
        {
            "mem_cube_id": cube,
            "name": body.templateName,
            "metadata": body.model_extra,
        },
    )
    identifier = data.get("profile_template_id") if isinstance(data, dict) else None
    if not isinstance(identifier, str) or not 1 <= len(identifier) <= 128:
        raise malformed()
    return Envelope(data={"templateId": identifier})


@router.post("/update", response_model=Envelope[dict])
async def update(body: Update, request: Request, response: Response):
    cube, upstream = scope(request, response, body.projectId, "owner")
    # Upstream validates tenant ownership atomically with its own mutation lock.
    data = await upstream.request(
        "update_profile_template",
        {
            "mem_cube_id": cube,
            "profile_template_id": body.templateId,
            "name": body.templateName,
            "metadata": body.model_extra,
        },
    )
    view(data, body.projectId, body.templateId)
    if data.get("success") is not True:
        raise malformed()
    return Envelope(data={})


@router.post("/delete", response_model=Envelope[dict])
async def delete(body: Identifier, request: Request, response: Response):
    cube, upstream = scope(request, response, body.projectId, "owner")
    data = await upstream.request(
        "delete_profile_template",
        {
            "mem_cube_id": cube,
            "profile_template_id": body.templateId,
        },
    )
    if (
        not isinstance(data, dict)
        or data.get("success") is not True
        or data.get("profile_template_id") != body.templateId
    ):
        raise malformed()
    return Envelope(data={})
