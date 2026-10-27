"""接口密钥：真实持久化 + 网关配置文件合成下发（指定权限=项目 owner，设计 task14 §5）。

成功即已写入（create/revoke 的响应成功 ⟺ 网关配置文件已更新）；文件与库在同一
事务链内落成，写失败随事务回滚并返回 503——绝不假成功（前端创建弹窗文案以此为准）。
"""

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from .auth import control_for
from .errors import ApiError
from .identity_store import missing
from .kb_models import Strict
from .key_store import ApiKeyStore, mutate_manifest, write_tenants_conf
from .models import Envelope
from .timefmt import display

router = APIRouter(prefix="/api/dashboard/keys")

NOT_CONFIGURED = ApiError(50300, "未配置网关交付路径，无法签发密钥", status_code=503)


def write_failure(verb: str) -> ApiError:
    return ApiError(50300, f"网关配置写入失败，密钥未{verb}", status_code=503)


class ListKeys(Strict):
    projectId: str = Field(min_length=1, max_length=128)
    pageNum: int = Field(1, ge=1, le=10000)
    pageSize: int = Field(10, ge=1, le=100)


class CreateKey(Strict):
    projectId: str = Field(min_length=1, max_length=128)
    keyName: str = Field(min_length=1, max_length=64)


class CopyKey(Strict):
    apiKeyId: str = Field(min_length=1, max_length=64)


class RenameKey(Strict):
    keyName: str = Field(min_length=1, max_length=64)


class KeyRecord(BaseModel):
    id: str
    keyName: str
    apiKey: str
    createTime: str


class KeyRecords(BaseModel):
    records: list[KeyRecord]
    total: int


def project_guard(store: ApiKeyStore, db, project_id: str) -> None:
    """可用项目不存在/不可见 → 404；停用/归档 → 404；不足 owner → 403（kb_store.project 同款）。"""
    row = store.control._row(db, project_id)
    if row["disabled"] or row["archived"]:
        raise missing()
    store.control.require_role(project_id, "owner", db)


def key_guard(store: ApiKeyStore, db, key_id: str) -> dict:
    """未知/已撤销 → 404；其项目不可用 → 404；非该项目的 owner → 403。"""
    row = store.get(db, key_id)
    if row is None or row["status"] != "active":
        raise missing()
    project_guard(store, db, row["project_id"])
    return row


def manifest_path(request: Request):
    return request.app.state.settings.gateway_keys_file


def as_record(row: dict) -> KeyRecord:
    return KeyRecord(
        id=row["id"],
        keyName=row["name"],
        apiKey=row["token"],
        createTime=display(row["create_time"]),
    )


def actor_id(request: Request) -> str | None:
    actor = request.scope.get("identity")
    return actor["id"] if actor else None


@router.post("/list", response_model=Envelope[KeyRecords])
def list_keys(body: ListKeys, request: Request, response: Response):
    store = ApiKeyStore(control_for(request))
    with store.control.connection() as db:
        project_guard(store, db, body.projectId)
        records, total = store.page(db, body.projectId, body.pageNum, body.pageSize)
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data=KeyRecords(records=[as_record(row) for row in records], total=total))


@router.post("/create", response_model=Envelope[KeyRecord])
def create_key(body: CreateKey, request: Request, response: Response):
    path = manifest_path(request)
    store = ApiKeyStore(control_for(request))
    if path is None:
        raise NOT_CONFIGURED
    try:

        def change(db):
            project_guard(store, db, body.projectId)
            return store.insert(db, body.projectId, body.keyName, actor_id(request))

        row = mutate_manifest(store, path, change, writer=write_tenants_conf)
    except ApiError:
        raise
    except (OSError, ValueError) as error:
        raise write_failure("创建") from error
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data=as_record(row))


@router.post("/revoke/{id}", response_model=Envelope[dict])
def revoke_key(id: str, request: Request, response: Response):
    path = manifest_path(request)
    store = ApiKeyStore(control_for(request))
    if path is None:
        raise NOT_CONFIGURED
    try:

        def change(db):
            key_guard(store, db, id)
            return store.mark_revoked(db, id, actor_id(request))

        mutate_manifest(store, path, change, writer=write_tenants_conf)
    except ApiError:
        raise
    except (OSError, ValueError) as error:
        raise write_failure("撤销") from error
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data={"ok": True})


@router.post("/copy", response_model=Envelope[str])
def copy_key(body: CopyKey, request: Request, response: Response):
    store = ApiKeyStore(control_for(request))
    with store.control.connection() as db:
        row = key_guard(store, db, body.apiKeyId)
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data=row["token"])


@router.post("/rename/{id}", response_model=Envelope[KeyRecord])
def rename_key(body: RenameKey, id: str, request: Request, response: Response):
    store = ApiKeyStore(control_for(request))
    with store.control.connection() as db, db:
        db.execute("BEGIN IMMEDIATE")
        key_guard(store, db, id)
        row = store.rename(db, id, body.keyName)
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data=as_record(row))
