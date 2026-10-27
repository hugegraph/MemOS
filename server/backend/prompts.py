"""Project extraction rules are persisted and executed by the real upstream reader."""

from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import Field, field_validator

from .auth import control_for
from .kb_models import Strict
from .kb_store import KnowledgeStore
from .memory_models import TAB_TYPES
from .models import Envelope
from .product_client import malformed

router = APIRouter(prefix="/api/dashboard/prompt-config")
TYPES = ("text_mem", "pref_mem", "event_mem")


class Project(Strict):
    projectId: str = Field(min_length=1, max_length=128)


class Save(Project):
    prompt: str = Field(min_length=1, max_length=20000)

    @field_validator("prompt")
    @classmethod
    def valid(cls, value):
        value = value.strip()
        if "${conversation}" not in value:
            raise ValueError("Preserve the conversation placeholder")
        return value


class Generate(Project):
    description: str = Field(min_length=1, max_length=2000)

    @field_validator("description")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Expected a description")
        return value.strip()


class Message(Strict):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=20000)


class Debug(Project):
    messages: list[Message] = Field(min_length=1, max_length=30)
    extractionTypes: list[Literal["text_mem", "pref_mem", "event_mem"]] | None = Field(
        None, max_length=3
    )

    @field_validator("extractionTypes")
    @classmethod
    def unique(cls, value):
        if value and len(value) != len(set(value)):
            raise ValueError("Duplicate types")
        return value or None


def scope(request, response, project, role):
    store = control_for(request)
    cube = KnowledgeStore.project(store, project, role)
    response.headers["Cache-Control"] = "no-store"
    return cube, request.app.state.product


def prompt_value(data):
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("prompt"), str)
        or not 1 <= len(data["prompt"]) <= 20000
        or "${conversation}" not in data["prompt"]
    ):
        raise malformed()
    return data["prompt"]


@router.post("/detail", response_model=Envelope[dict])
async def read(body: Project, request: Request, response: Response):
    cube, up = scope(request, response, body.projectId, "viewer")
    data = await up.request("get_extract_prompt", {"mem_cube_id": cube}, code=200)
    if not isinstance(data, dict) or data.get("mem_cube_id") != cube:
        raise malformed()
    result = {"prompt": prompt_value(data), "projectId": body.projectId}
    if data.get("updated_at") is not None:
        if not isinstance(data["updated_at"], str):
            raise malformed()
        result["updatedAt"] = data["updated_at"]
    return Envelope(data=result)


@router.post("/save", response_model=Envelope[dict])
async def save(body: Save, request: Request, response: Response):
    cube, up = scope(request, response, body.projectId, "owner")
    data = await up.request(
        "save_extract_prompt", {"mem_cube_id": cube, "prompt": body.prompt}, code=200
    )
    if prompt_value(data) != body.prompt:
        raise malformed()
    return Envelope(data={"ok": True})


@router.post("/reset-default", response_model=Envelope[dict])
async def reset(body: Project, request: Request, response: Response):
    cube, up = scope(request, response, body.projectId, "owner")
    data = await up.request("reset_extract_prompt", {"mem_cube_id": cube}, code=200)
    return Envelope(data={"prompt": prompt_value(data)})


@router.post("/generate", response_model=Envelope[dict])
async def generate(body: Generate, request: Request, response: Response):
    cube, up = scope(request, response, body.projectId, "editor")
    data = await up.request(
        "generate_extract_prompt",
        {"mem_cube_id": cube, "description": body.description},
        code=200,
        timeout=request.app.state.settings.model_timeout_seconds,
    )
    return Envelope(data={"prompt": prompt_value(data)})


@router.post("/debug/extract", response_model=Envelope[dict])
async def debug(body: Debug, request: Request, response: Response):
    cube, up = scope(request, response, body.projectId, "editor")
    types = body.extractionTypes or list(TYPES)
    data = await up.request(
        "debug_extract",
        {
            "mem_cube_id": cube,
            "messages": [m.model_dump() for m in body.messages],
            "extraction_types": types,
        },
        code=200,
        timeout=request.app.state.settings.model_timeout_seconds,
    )
    if (
        not isinstance(data, dict)
        or data.get("extraction_types") != types
        or not isinstance(data.get("results"), list)
        or len(data["results"]) > 100
    ):
        raise malformed()
    results = []
    native = {kind: tab for tab, kinds in TAB_TYPES.items() for kind in kinds}
    for row in data["results"]:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("type"), str)
            or not isinstance(row.get("content"), str)
            or len(row["content"]) > 20000
        ):
            raise malformed()
        mapped = native.get(row["type"], row["type"] if row["type"] in TAB_TYPES else "raw")
        if mapped != "raw" and mapped not in types:
            continue
        results.append({"type": mapped, "content": row["content"]})
    return Envelope(data={"extractionTypes": types, "results": results})
