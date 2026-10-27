"""Read-only graph and model projections from the tenant-scoped upstream service."""

import json
import re
from collections import Counter
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Query, Request, Response

from .auth import control_for
from .errors import ApiError
from .identity_store import missing
from .memory_models import TAB_TYPES
from .models import Envelope
from .product_client import malformed
from .profile_graph import PROFILE_ID, profile_fulltext, profile_nodes
from .project_context import selected_project
from .timefmt import display

router = APIRouter(prefix="/api/dashboard")
NATIVE_TYPES = frozenset(kind for kinds in TAB_TYPES.values() for kind in kinds)
NODE_BYTES = 1024 * 1024
EDGE_LIMIT = 8000


def text(value, maximum=1024):
    if not isinstance(value, str) or len(value) > maximum:
        raise malformed()
    return value


def scope(request, response, tenant):
    store = control_for(request)
    project = tenant if tenant is not None else selected_project(request)
    cube = store.resolve_cube(project)
    if cube is None:
        raise missing()
    store.require_role(project, "viewer")
    response.headers["Cache-Control"] = "no-store"
    return project, cube


def validate_graph_data(data, cube):
    if (
        not isinstance(data, dict)
        or data.get("cube_id") != cube
        or not isinstance(data.get("nodes"), list)
        or not isinstance(data.get("edges"), list)
        or len(data["nodes"]) > 10000
        or len(data["edges"]) > 100000
        or type(data.get("total_nodes")) is not int
        or data["total_nodes"] < 0
        or type(data.get("truncated")) is not bool
    ):
        raise malformed()


def project_graph(data, cube, project, requested, limit):
    validate_graph_data(data, cube)
    truncated = data["truncated"] or data["total_nodes"] > len(data["nodes"])
    nodes = []
    ids = set()
    budget = 0
    for row in data["nodes"]:
        if not isinstance(row, dict):
            raise malformed()
        identifier = text(row.get("id"), 1024)
        if not identifier or identifier in ids:
            raise malformed()
        ids.add(identifier)
        status = text(row.get("status", ""), 64)
        kind = text(row.get("memoryType", ""), 64)
        if (
            status != "activated"
            or kind not in NATIVE_TYPES
            or (requested and kind not in requested)
        ):
            continue
        memory = text(row.get("memory"), 20000)
        flag = row.get("memoryTruncated")
        if type(flag) is not bool:
            raise malformed()
        sources = row.get("sourceMemoryIds", [])
        if not isinstance(sources, list) or len(sources) > 10000:
            raise malformed()
        sources = list(dict.fromkeys(text(s, 256) for s in sources))
        try:
            created_at = display(text(row.get("createdAt", ""), 128))
        except (ValueError, OverflowError) as exc:
            raise malformed() from exc
        # Preserve provenance IDs; draw derived edges only when both endpoints are visible.
        node = {
            "id": identifier,
            "label": memory[:119] + "…" if len(memory) > 120 else memory,
            "labelTruncated": flag or len(memory) > 120,
            "memoryType": kind,
            "createdAt": created_at,
            "sessionId": text(row.get("sessionId", ""), 256),
            "clusterId": identifier,
            "userId": text(row.get("userId", ""), 256),
            "sourceIds": sources,
        }
        size = len(json.dumps(node, ensure_ascii=False).encode())
        if len(nodes) >= limit or budget + size > NODE_BYTES:
            truncated = True
            continue
        budget += size
        nodes.append(node)
    by_id = {node["id"]: node for node in nodes}
    links = []
    seen = set()
    edge_bytes = 0
    parents = {identifier: identifier for identifier in by_id}
    degrees = Counter()

    def root(identifier):
        while parents[identifier] != identifier:
            parents[identifier] = parents[parents[identifier]]
            identifier = parents[identifier]
        return identifier

    def link(source, target, kind):
        nonlocal truncated, edge_bytes
        if source not in by_id or target not in by_id or source == target:
            return
        key = (source, target, kind)
        if key in seen:
            return
        projected = {"source": source, "target": target, "kind": kind}
        size = len(json.dumps(projected, ensure_ascii=False).encode())
        if len(links) >= EDGE_LIMIT or edge_bytes + size > 750 * 1024:
            truncated = True
            return
        seen.add(key)
        links.append(projected)
        edge_bytes += size
        degrees.update((source, target))
        a, b = root(source), root(target)
        parents[max(a, b)] = min(a, b)

    for edge in data["edges"]:
        if not isinstance(edge, dict):
            raise malformed()
        source = text(edge.get("source"), 1024)
        target = text(edge.get("target"), 1024)
        kind = text(edge.get("type"), 64)
        mapped = {"PARENT": "parent", "MERGED_TO": "merged"}.get(kind)
        if mapped:
            link(source, target, mapped)
    sessions = {}
    for node in nodes:
        for source in node["sourceIds"]:
            link(source, node["id"], "derived")
        session = node["sessionId"]
        if session:
            if session in sessions:
                link(sessions[session], node["id"], "session")
            else:
                sessions[session] = node["id"]
    clusters = set()
    for node in nodes:
        if degrees[node["id"]]:
            node["clusterId"] = root(node["id"])
            clusters.add(node["clusterId"])
    types = Counter(node["memoryType"] for node in nodes)
    return {
        "tenant": project,
        "nodes": nodes,
        "links": links,
        "stats": {
            "nodes": len(nodes),
            "links": len(links),
            "sessions": len(sessions),
            "clusters": len(clusters),
            "orphans": sum(not degrees[n["id"]] for n in nodes),
            "truncated": truncated,
            "types": [{"name": name, "count": count} for name, count in sorted(types.items())],
        },
    }


@router.get("/graph/memories", response_model=Envelope[dict])
async def graph(
    request: Request,
    response: Response,
    tenant: str | None = Query(None, min_length=1, max_length=128),
    types: str | None = Query(None, max_length=1024),
    limit: int = Query(2000, ge=1, le=2000),
):
    project, cube = scope(request, response, tenant)
    requested = list(dict.fromkeys(part.strip() for part in types.split(","))) if types else []
    if any(kind not in NATIVE_TYPES for kind in requested):
        raise ApiError(40000, "Unknown memory type", status_code=400)
    upstream = request.app.state.product
    graph_types = (
        [kind for kind in requested if kind != "ProfileMemory"]
        if requested
        else sorted(NATIVE_TYPES - {"ProfileMemory"})
    )
    data = (
        await upstream.request(
            "get_memory_graph",
            {
                "mem_cube_id": cube,
                "memory_types": graph_types,
                "status": ["activated"],
                "limit": limit,
                "label_max": 120,
                "include_edges": True,
            },
            code=200,
        )
        if graph_types
        else {
            "cube_id": cube,
            "nodes": [],
            "edges": [],
            "total_nodes": 0,
            "truncated": False,
        }
    )
    validate_graph_data(data, cube)
    if not requested or "ProfileMemory" in requested:
        fields, total = await profile_nodes(upstream, cube, max(0, limit - len(data["nodes"])))
        data = {
            **data,
            "nodes": [*data["nodes"], *fields],
            "total_nodes": data["total_nodes"] + total,
            "truncated": data["truncated"] or total > len(fields),
        }
    return Envelope(data=project_graph(data, cube, project, requested, limit))


@router.get("/graph/memory", response_model=Envelope[dict])
async def fulltext(
    request: Request,
    response: Response,
    id: str = Query(min_length=1, max_length=1024),
    tenant: str | None = Query(None, min_length=1, max_length=128),
):
    _, cube = scope(request, response, tenant)
    if PROFILE_ID.fullmatch(id):
        return Envelope(data=await profile_fulltext(request.app.state.product, cube, id))
    data = await request.app.state.product.request(
        "get_memory_by_ids?" + urlencode({"mem_cube_id": cube}),
        {"memory_ids": [id], "include_sources": False},
        code=200,
    )
    if not isinstance(data, dict) or not isinstance(data.get("memories"), list):
        raise malformed()
    if not data["memories"]:
        raise missing()
    if len(data["memories"]) != 1:
        raise malformed()
    row = data["memories"][0]
    if (
        not isinstance(row, dict)
        or row.get("id") != id
        or not isinstance(row.get("metadata"), dict)
    ):
        raise malformed()
    meta = row["metadata"]
    text(meta.get("status", ""), 64)
    text(meta.get("memory_type", ""), 64)
    if (
        meta.get("status") != "activated"
        or meta.get("memory_type") not in NATIVE_TYPES
        or (meta.get("user_name") is not None and meta["user_name"] != cube)
    ):
        raise missing()
    memory = row.get("memory")
    if not isinstance(memory, str):
        raise malformed()
    if len(json.dumps({"memory": memory}, ensure_ascii=False).encode()) > 1024 * 1024:
        raise ApiError(41300, "Memory body exceeds the view limit", status_code=413)
    return Envelope(data={"id": id, "memory": memory})


MODEL_SOURCES = {
    "MEMRADER_MODEL": ("记忆读取", "memory"),
    "MOS_CHAT_MODEL": ("记忆写入 / 对话", "chat"),
    "MOS_EMBEDDER_MODEL": ("向量化", "memory"),
    "MOS_RERANKER_MODEL": ("重排", "memory"),
}


def model_spec(source, value):
    value = text(value, 2048)
    if source == "MOS_EMBEDDER_MODEL":
        match = re.fullmatch(r"([1-9][0-9]{0,4})\s*维", value)
        return f"{match[1]} 维" if match and int(match[1]) <= 65536 else ""
    if source == "MOS_RERANKER_MODEL":
        match = re.fullmatch(r"后端\s+(cosine_local|http_bge|http_bge_strategy|concat|noop)", value)
        return f"后端 {match[1]}" if match else ""
    if not value:
        return ""
    try:
        url = urlsplit(value if "://" in value else "//" + value)
        host, port = url.hostname, url.port
        if not host or not re.fullmatch(r"[A-Za-z0-9.:-]+", host):
            return ""
        if ":" in host:
            host = "[" + host + "]"
        return host + (f":{port}" if port is not None else "")
    except ValueError:
        return ""


@router.get("/models", response_model=Envelope[dict])
async def models(request: Request, response: Response):
    # Model inventory is deployment-wide and read-only for authenticated accounts.
    response.headers["Cache-Control"] = "no-store"
    data = await request.app.state.product.request("models", method="GET", code=200)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("records"), list)
        or len(data["records"]) > 100
    ):
        raise malformed()
    records = []
    seen = set()
    for row in data["records"]:
        if not isinstance(row, dict):
            raise malformed()
        source = text(row.get("source"), 128)
        if source not in MODEL_SOURCES:
            continue
        if source in seen:
            raise malformed()
        seen.add(source)
        model = text(row.get("model"), 256)
        if (
            not re.fullmatch(r"[A-Za-z0-9_./:-]{1,256}", model)
            or "://" in model
            or re.search(r"(?:^|[/_:])sk-[A-Za-z0-9_-]{12,}", model)
        ):
            raise malformed()
        role, group = MODEL_SOURCES[source]
        records.append(
            {
                "role": role,
                "group": group,
                "source": source,
                "model": model,
                "spec": model_spec(source, row.get("spec", "")),
            }
        )
    return Envelope(data={"records": records, "total": len(records)})
