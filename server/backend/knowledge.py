"""Upstream-backed KB entities with local, permission-checked workspace sharing."""

from fastapi import APIRouter, Depends, Request, Response

from .auth import control_for
from .errors import ApiError
from .identity_store import missing
from .kb_models import Associate, KBCreate, KBDelete, KBDetail, KBList, KBUpdate, Remove
from .models import Envelope
from .product_client import malformed
from .project_context import selected_project
from .timefmt import display

router = APIRouter(prefix="/api/dashboard/knowledgebase")


async def mutation(request: Request):
    async with request.app.state.knowledge.mutation:
        yield


def kind(store, project):
    with store.connection() as db:
        return store._row(db, project)["kind"]


def parts(request, response):
    response.headers["Cache-Control"] = "no-store"
    return control_for(request), request.app.state.knowledge, request.app.state.product


def view(row, projects):
    if (
        not isinstance(row, dict)
        or not isinstance(row.get("knowledgebase_id"), str)
        or not isinstance(row.get("knowledgebase_name"), str)
    ):
        raise malformed()
    if row.get("knowledgebase_description") is not None and not isinstance(
        row["knowledgebase_description"], str
    ):
        raise malformed()
    result = {
        "kbId": row["knowledgebase_id"],
        "knowledgebaseName": row["knowledgebase_name"],
        "knowledgebaseDescription": row.get("knowledgebase_description") or "",
        "projectIds": projects,
    }
    for source, target, convert in [
        ("file_count", "fileCount", None),
        ("total_size", "totalSize", None),
        ("created_at", "createTime", display),
        ("updated_at", "updateTime", display),
    ]:
        value = row.get(source)
        if value is not None:
            if source in {"file_count", "total_size"} and (type(value) is not int or value < 0):
                raise malformed()
            if source.endswith("_at") and not isinstance(value, str):
                raise malformed()
            result[target] = value if convert is None else convert(value)
    return result


async def detail(request, identifier, project=None):
    store = control_for(request)
    registry = request.app.state.knowledge
    with store.connection() as db:
        registered = db.execute("SELECT 1 FROM kb_registry WHERE id=?", (identifier,)).fetchone()
    if not registered:
        for workspace in store.dictionary():
            await discover(request, workspace["id"])
    _, cube, projects = registry.access(store, identifier, project=project)
    row = await request.app.state.product.request(
        "get_knowledgebase", {"mem_cube_id": cube, "knowledgebase_id": identifier}
    )
    result = view(row, projects)
    if result["kbId"] != identifier:
        raise malformed()
    return result


async def discover(request, project):
    store = control_for(request)
    registry = request.app.state.knowledge
    cube = registry.project(store, project)
    page = 1
    seen = set()
    while True:
        data = await request.app.state.product.request(
            "get_knowledgebase", {"mem_cube_id": cube, "page": page, "page_size": 200}
        )
        rows = data.get("knowledgebase_list") if isinstance(data, dict) else None
        total = data.get("total") if isinstance(data, dict) else None
        if not isinstance(rows, list) or type(total) is not int or total < 0 or len(rows) > 200:
            raise malformed()
        for row in rows:
            item = view(row, [project])
            identifier = item["kbId"]
            if identifier in seen:
                raise malformed()
            seen.add(identifier)
            registry.register(identifier, project)
        if len(seen) >= total:
            return
        if not rows or page >= 50:
            raise malformed()
        page += 1


@router.post("/list", response_model=Envelope[dict])
async def listing(body: KBList, request: Request, response: Response):
    store, registry, _ = parts(request, response)
    projects = [body.projectId] if body.projectId else [p["id"] for p in store.dictionary()]
    for project in projects:
        await discover(request, project)
    records = []
    for identifier in registry.ids(store, body.projectId):
        try:
            item = await detail(request, identifier, body.projectId)
        except ApiError as exc:
            if exc.status_code == 404:
                continue
            raise
        if body.name.casefold() in item["knowledgebaseName"].casefold():
            records.append(item)
    offset = (body.pageNum - 1) * body.pageSize
    return Envelope(
        data={"records": records[offset : offset + body.pageSize], "total": len(records)}
    )


@router.post("/create", response_model=Envelope[dict], dependencies=[Depends(mutation)])
async def create(body: KBCreate, request: Request, response: Response):
    store, registry, up = parts(request, response)
    home = body.projectId or (body.projectIds or [selected_project(request)])[0]
    if not home:
        raise missing()
    cube = registry.project(store, home, "editor")
    targets = body.projectIds if body.projectIds is not None else [home]
    # Validate all targets before creating anything upstream.
    if set(targets) != {home}:
        for target in set(targets) | {home}:
            registry.project(store, target, "owner")
            if target != home and (
                kind(store, home) == "personal" or kind(store, target) == "personal"
            ):
                raise ApiError(40900, "个人空间知识库不跨项目共享", status_code=409)
    row = await up.request(
        "create_knowledgebase",
        {
            "mem_cube_id": cube,
            "knowledgebase_name": body.knowledgebaseName,
            "knowledgebase_description": body.knowledgebaseDescription,
        },
    )
    item = view(row, [home])
    identifier = item["kbId"]
    registry.register(identifier, home)
    if set(targets) != {home}:
        registry.links(store, identifier, targets)
    return Envelope(data=await detail(request, identifier))


@router.post("/detail", response_model=Envelope[dict])
async def read(body: KBDetail, request: Request, response: Response):
    parts(request, response)
    return Envelope(data=await detail(request, body.knowledgeBaseId, body.projectId))


@router.post("/update", response_model=Envelope[dict], dependencies=[Depends(mutation)])
async def update(body: KBUpdate, request: Request, response: Response):
    store, registry, up = parts(request, response)
    async with registry.locks[body.id]:
        home, cube, _ = registry.access(store, body.id, project=body.projectId, write="editor")
        if body.projectIds is not None:
            # Permission preflight without mutating associations.
            for target in set(body.projectIds) | {home}:
                registry.project(store, target, "owner")
            registry.access(store, body.id, write="owner")
        payload = {"mem_cube_id": cube, "knowledgebase_id": body.id}
        for name in ("knowledgebaseName", "knowledgebaseDescription"):
            if name in body.model_fields_set:
                payload[
                    {
                        "knowledgebaseName": "knowledgebase_name",
                        "knowledgebaseDescription": "knowledgebase_description",
                    }[name]
                ] = getattr(body, name)
        if len(payload) > 2:
            row = await up.request("update_knowledgebase", payload)
            if view(row, [])["kbId"] != body.id:
                raise malformed()
        if body.projectIds is not None:
            registry.links(store, body.id, body.projectIds)
        return Envelope(data=await detail(request, body.id))


async def erase(body, request):
    store = control_for(request)
    registry = request.app.state.knowledge
    async with registry.locks[body.knowledgeBaseId]:
        _, cube, _ = registry.access(
            store, body.knowledgeBaseId, project=body.projectId, write="owner", pending=True
        )
        with store.connection() as db:
            db.execute(
                "UPDATE kb_registry SET state='deleting' WHERE id=?", (body.knowledgeBaseId,)
            )
        try:
            data = await request.app.state.product.request(
                "delete_knowledgebase",
                {"mem_cube_id": cube, "knowledgebase_id": body.knowledgeBaseId, "delete_all": True},
            )
            if not isinstance(data, dict) or data.get("success") is not True:
                raise malformed()
        except ApiError as exc:
            if exc.status_code != 404:
                raise
        with store.connection() as db:
            db.execute("DELETE FROM kb_registry WHERE id=?", (body.knowledgeBaseId,))
        return {"ok": True, "id": body.knowledgeBaseId}


@router.post("/delete", response_model=Envelope[dict], dependencies=[Depends(mutation)])
async def delete(body: KBDelete, request: Request, response: Response):
    parts(request, response)
    return Envelope(data=await erase(body, request))


@router.post("/project/associate", response_model=Envelope[dict], dependencies=[Depends(mutation)])
async def associate(body: Associate, request: Request, response: Response):
    store, registry, _ = parts(request, response)
    registry.project(store, body.projectId, "owner")
    # Whole replacement is one local transaction; every ID is authorized before changes.
    old = set(registry.ids(store, body.projectId))
    new = set(body.knowledgeBaseIds)
    plans = []
    for identifier in sorted(old | new):
        home, _, _ = registry.access(store, identifier, write="owner")
        await detail(request, identifier)
        with store.connection() as db:
            targets = {
                r[0]
                for r in db.execute("SELECT project_id FROM kb_links WHERE kb_id=?", (identifier,))
            }
        targets.discard(body.projectId)
        if identifier in new:
            targets.add(body.projectId)
        for target in targets | {home}:
            registry.project(store, target, "owner")
        plans.append((identifier, targets))
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        # Validate original and replacement scopes again under the write transaction.
        for identifier, targets in plans:
            row = db.execute(
                "SELECT home_project,state FROM kb_registry WHERE id=?", (identifier,)
            ).fetchone()
            if row is None or row["state"] != "active":
                raise missing()
            old_targets = {
                r[0]
                for r in db.execute("SELECT project_id FROM kb_links WHERE kb_id=?", (identifier,))
            }
            for target in old_targets | targets | {row["home_project"]}:
                registry.project(store, target, "owner", db)
                if target != row["home_project"] and (
                    store._row(db, target)["kind"] == "personal"
                    or store._row(db, row["home_project"])["kind"] == "personal"
                ):
                    raise ApiError(40900, "个人空间知识库不跨项目共享", status_code=409)
            db.execute("DELETE FROM kb_links WHERE kb_id=?", (identifier,))
            db.executemany("INSERT INTO kb_links VALUES (?,?)", [(identifier, p) for p in targets])
    return Envelope(data={"ok": True})


@router.post("/project/remove", response_model=Envelope[dict], dependencies=[Depends(mutation)])
async def remove(body: Remove, request: Request, response: Response):
    store, registry, _ = parts(request, response)
    if body.deleteAll:
        await erase(body, request)
    else:
        async with registry.locks[body.knowledgeBaseId]:
            registry.access(store, body.knowledgeBaseId, project=body.projectId, write="owner")
            with store.connection() as db:
                targets = [
                    r[0]
                    for r in db.execute(
                        "SELECT project_id FROM kb_links WHERE kb_id=? AND project_id<>?",
                        (body.knowledgeBaseId, body.projectId),
                    )
                ]
            registry.links(store, body.knowledgeBaseId, targets)
    return Envelope(data={"ok": True})
