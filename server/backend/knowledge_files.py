"""Document bytes stay upstream; uploads never instruct the upstream to fetch URLs."""

import base64
import binascii
import re
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field, field_validator, model_validator

from .auth import control_for
from .errors import ApiError
from .identity_store import missing
from .kb_models import KBDelete, Page, Strict
from .knowledge import detail, mutation
from .memory_models import time_boundary
from .models import Envelope
from .product_client import malformed
from .timefmt import display

MAX_FILE_BYTES = 8 * 1024 * 1024
EXTENSIONS = {"pdf", "doc", "docx", "txt", "md", "json", "xml"}
STATUSES = {
    "stored": "available",
    "failed": "processFailed",
    "processing": "processing",
    "pending": "waiting",
}
router = APIRouter(prefix="/api/dashboard/knowledgebase/file")


def bad_file():
    return ApiError(40000, "文件名、格式或内容无效", status_code=400)


class FilePayload(Strict):
    name: str = Field(min_length=1, max_length=255)
    content: str = Field(max_length=12 * 1024 * 1024)
    type: str = Field("", max_length=128)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value):
        if (
            value in {".", ".."}
            or any(c in value for c in "/\\:")
            or value != value.strip()
            or value.endswith(".")
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or PurePosixPath(value).suffix.lower().lstrip(".") not in EXTENSIONS
        ):
            raise ValueError("Invalid document name")
        return value

    @field_validator("type")
    @classmethod
    def document_type(cls, value):
        if value == "skill" or any(ord(c) < 32 for c in value):
            raise ValueError("Only ordinary document uploads are supported")
        return value

    def bytes(self):
        if self.content.startswith("data:"):
            match = re.fullmatch(
                r"data:([A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+)?;base64,([A-Za-z0-9+/=]*)", self.content
            )
            if not match:
                raise bad_file()
            try:
                raw = base64.b64decode(match[2], validate=True)
            except (ValueError, binascii.Error) as exc:
                raise bad_file() from exc
        else:
            raw = self.content.encode("utf-8")
        if len(raw) > MAX_FILE_BYTES:
            raise ApiError(41300, "文件超过8MiB限制", status_code=413)
        return raw


class Upload(KBDelete):
    file: FilePayload


class FileList(Page):
    knowledgeBaseId: str = Field(min_length=1, max_length=128)
    fileName: str = Field("", max_length=255)
    type: str = Field("", max_length=128)
    status: (
        Literal[
            "waiting", "uploading", "processing", "available", "deleting", "failed", "processFailed"
        ]
        | None
    ) = None
    startTime: str | None = Field(None, max_length=64)
    endTime: str | None = Field(None, max_length=64)

    @field_validator("type")
    @classmethod
    def file_type(cls, value):
        if value and value not in EXTENSIONS | {
            "document",
            "text/plain",
            "text/markdown",
            "application/pdf",
            "application/json",
            "application/xml",
            "text/xml",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }:
            raise ValueError("Unsupported file type filter")
        return value

    @model_validator(mode="after")
    def times(self):
        try:
            start, _ = time_boundary(self.startTime)
            end, exclusive = time_boundary(self.endTime, end=True)
            if start and end and (start > end or (exclusive and start == end)):
                raise ValueError("Invalid time range")
        except (OverflowError, ValueError) as exc:
            raise ValueError("Invalid time boundary") from exc
        return self


class DeleteFiles(Strict):
    projectId: str = Field(min_length=1, max_length=128)
    fileIds: list[str] = Field(min_length=1, max_length=100)

    @field_validator("fileIds")
    @classmethod
    def unique(cls, value):
        if len(set(value)) != len(value) or any(not x or len(x) > 128 for x in value):
            raise ValueError("Invalid IDs")
        return value


def file_view(row, kb):
    if (
        not isinstance(row, dict)
        or not isinstance(row.get("id"), str)
        or row.get("knowledgebase_id") != kb
        or not isinstance(row.get("name"), str)
        or type(row.get("size")) is not int
        or row["size"] < 0
    ):
        raise malformed()
    try:
        FilePayload.safe_name(row["name"])
    except ValueError as exc:
        raise malformed() from exc
    status = STATUSES.get(row.get("status"), row.get("status"))
    if status not in {
        "waiting",
        "uploading",
        "processing",
        "available",
        "deleting",
        "failed",
        "processFailed",
    }:
        raise malformed()
    result = {
        "id": row["id"],
        "fileId": row["id"],
        "knowledgebaseId": kb,
        "name": row["name"],
        "size": row["size"],
        "sizeMB": round(row["size"] / (1024 * 1024), 6),
        "type": PurePosixPath(row["name"]).suffix.lower().lstrip("."),
        "status": status,
    }
    if row.get("created_at") is not None:
        if not isinstance(row["created_at"], str):
            raise malformed()
        try:
            time_boundary(row["created_at"])
        except (ValueError, OverflowError) as exc:
            raise malformed() from exc
        result["createTime"] = display(row["created_at"])
    return result


def remember(request, records):
    with request.app.state.control.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for item in records:
            old = db.execute("SELECT kb_id FROM kb_files WHERE id=?", (item["id"],)).fetchone()
            if old and old[0] != item["knowledgebaseId"]:
                raise malformed()
            db.execute(
                "INSERT INTO kb_files(id,kb_id) VALUES (?,?) ON CONFLICT(id) DO NOTHING",
                (item["id"], item["knowledgebaseId"]),
            )


async def source(request, cube, *, kb=None, ids=None):
    rows = []
    seen = set()
    page = 1
    while True:
        body = {"mem_cube_id": cube, "page": page, "page_size": 200}
        if kb:
            body["knowledgebase_id"] = kb
        if ids:
            body["file_ids"] = ids
        data = await request.app.state.product.request("get_knowledgebase_file", body)
        current = data.get("file_detail_list") if isinstance(data, dict) else None
        total = data.get("total") if isinstance(data, dict) else None
        if (
            not isinstance(current, list)
            or type(total) is not int
            or total < 0
            or len(current) > 200
        ):
            raise malformed()
        for row in current:
            if (
                not isinstance(row, dict)
                or row.get("id") in seen
                or not isinstance(row.get("id"), str)
            ):
                raise malformed()
            if ids and row["id"] not in ids:
                raise malformed()
            seen.add(row["id"])
            rows.append(row)
        if len(rows) >= total:
            return rows
        if not current or page >= 50:
            raise malformed()
        page += 1


async def file_meta(request, identifier, project, *, for_delete=False):
    store = control_for(request)
    registry = request.app.state.knowledge
    registry.project(store, project)
    with store.connection() as db:
        cached = db.execute("SELECT * FROM kb_files WHERE id=?", (identifier,)).fetchone()
    if cached:
        kb = cached["kb_id"]
        _, cube, _ = registry.access(
            store, kb, project=project, write="editor" if for_delete else None
        )
        if cached["state"] == "deleting" and not for_delete:
            raise ApiError(40900, "文件删除尚未确认，请重试删除", status_code=409)
        if cached["state"] == "deleted":
            if for_delete:
                return None, cube, kb
            raise missing()
        rows = await source(request, cube, kb=kb, ids=[identifier])
    else:
        allowed = {}
        for kb in registry.ids(store, project):
            _, cube, _ = registry.access(store, kb, project=project)
            allowed.setdefault(cube, set()).add(kb)
        rows = []
        for cube, kbs in allowed.items():
            candidates = await source(request, cube, ids=[identifier])
            rows = [r for r in candidates if r.get("knowledgebase_id") in kbs]
            if rows:
                break
        if not rows:
            raise missing()
        kb = rows[0]["knowledgebase_id"]
        registry.access(store, kb, project=project, write="editor" if for_delete else None)
    if not rows and cached and for_delete:
        with store.connection() as db:
            db.execute("UPDATE kb_files SET state='deleted' WHERE id=?", (identifier,))
        return None, cube, kb
    if len(rows) != 1:
        raise missing() if not rows else malformed()
    item = file_view(rows[0], kb)
    remember(request, [item])
    return item, cube, kb


@router.post("/add", response_model=Envelope[dict], dependencies=[Depends(mutation)])
async def upload(body: Upload, request: Request, response: Response):
    store = control_for(request)
    registry = request.app.state.knowledge
    _, cube, _ = registry.access(
        store, body.knowledgeBaseId, project=body.projectId, write="editor"
    )
    await detail(request, body.knowledgeBaseId, body.projectId)
    raw = body.file.bytes()
    data = await request.app.state.product.request(
        "add_knowledgebase_file",
        {
            "mem_cube_id": cube,
            "knowledgebase_id": body.knowledgeBaseId,
            "file": [
                {
                    "name": body.file.name,
                    "base64": base64.b64encode(raw).decode(),
                    "type": "document",
                }
            ],
        },
    )
    if not isinstance(data, list) or len(data) != 1:
        raise malformed()
    records = [file_view(row, body.knowledgeBaseId) for row in data]
    if records[0]["size"] != len(raw) or records[0]["name"] != body.file.name:
        raise malformed()
    remember(request, records)
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data={"records": records, "total": len(records)})


@router.post("/list", response_model=Envelope[dict])
async def listing(body: FileList, request: Request, response: Response):
    _, cube, _ = request.app.state.knowledge.access(
        control_for(request), body.knowledgeBaseId, project=body.projectId
    )
    await detail(request, body.knowledgeBaseId, body.projectId)
    rows = await source(request, cube, kb=body.knowledgeBaseId)
    records = [file_view(r, body.knowledgeBaseId) for r in rows]
    remember(request, records)
    start, _ = time_boundary(body.startTime)
    end, exclusive = time_boundary(body.endTime, end=True)
    if start and end and (start > end or (exclusive and start == end)):
        raise ApiError(40000, "时间范围无效", status_code=400)
    selected = []
    for row, item in zip(rows, records, strict=True):
        if body.fileName.casefold() not in item["name"].casefold():
            continue
        if body.status and body.status != item["status"]:
            continue
        if body.type and body.type != "document":
            extension = {
                "text/plain": "txt",
                "text/markdown": "md",
                "application/pdf": "pdf",
                "application/json": "json",
                "application/xml": "xml",
                "text/xml": "xml",
                "application/msword": "doc",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
            }.get(body.type, body.type)
            if item["type"] != extension:
                continue
        if start or end:
            # Compare source instants, never the timezone-less presentation string.
            created, _ = time_boundary(row.get("created_at"))
            if created is None:
                continue
            if start and created < start:
                continue
            if end and (created >= end if exclusive else created > end):
                continue
        selected.append(item)
    offset = (body.pageNum - 1) * body.pageSize
    response.headers["Cache-Control"] = "no-store"
    return Envelope(
        data={"records": selected[offset : offset + body.pageSize], "total": len(selected)}
    )


@router.get("/download")
async def download(
    request: Request,
    projectId: str = Query(min_length=1, max_length=128),
    fileId: str = Query(min_length=1, max_length=128),
):
    item, cube, _ = await file_meta(request, fileId, projectId)
    if item["size"] > MAX_FILE_BYTES:
        raise ApiError(41300, "下载文件超过8MiB限制", status_code=413)
    raw = await request.app.state.product.raw(
        "download_knowledgebase_file",
        {"mem_cube_id": cube, "file_id": fileId},
        limit=MAX_FILE_BYTES,
    )
    if len(raw) != item["size"]:
        raise malformed()
    filename = item["name"].replace('"', "")
    fallback = filename if filename.isascii() else "download"
    disposition = (
        f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"
    )
    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/delete", response_model=Envelope[dict], dependencies=[Depends(mutation)])
async def delete(body: DeleteFiles, request: Request, response: Response):
    # Validate every file and permission before deleting any byte.
    groups = {}
    for identifier in body.fileIds:
        item, cube, _ = await file_meta(request, identifier, body.projectId, for_delete=True)
        if item is not None:
            groups.setdefault(cube, []).append(identifier)
    deleted = 0
    for cube, identifiers in groups.items():
        with request.app.state.control.connection() as db:
            db.executemany(
                "UPDATE kb_files SET state='deleting' WHERE id=?", [(i,) for i in identifiers]
            )
        data = await request.app.state.product.request(
            "delete_knowledgebase_file", {"mem_cube_id": cube, "file_ids": identifiers}
        )
        if (
            not isinstance(data, dict)
            or type(data.get("deleted")) is not int
            or data["deleted"] != len(identifiers)
        ):
            raise malformed()
        deleted += data["deleted"]
        with request.app.state.control.connection() as db:
            db.executemany(
                "UPDATE kb_files SET state='deleted' WHERE id=?", [(i,) for i in identifiers]
            )
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data={"ok": True, "deleted": deleted})
