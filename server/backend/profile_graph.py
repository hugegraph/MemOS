"""Bounded, read-only profile field projections for the combined memory graph."""

import json
import re

from .errors import ApiError
from .identity_store import missing
from .memories import project_node
from .product_client import malformed

PROFILE_ID = re.compile(r"(profile_[0-9a-f]{32})_(.+)")


def profile_node(row, cube):
    if not isinstance(row, dict) or row.get("user_name") != cube:
        raise malformed()
    item = project_node(row, cube, "profile_mem", detail=True)
    identifier = item.memoryId
    match = PROFILE_ID.fullmatch(identifier)
    if (
        not match
        or len(identifier) > 1024
        or row.get("profile_instance_id") != match[1]
        or row.get("profile_path") != match[2]
    ):
        raise malformed()
    memory = item.memoryContent
    return {
        "id": identifier,
        "memory": memory[:120],
        "memoryTruncated": len(memory) > 120,
        "memoryType": "ProfileMemory",
        "status": "activated",
        "createdAt": row["created_at"],
        "sessionId": "",
        "userId": item.externalUserId or "",
        "sourceMemoryIds": [],
    }


async def profile_nodes(upstream, cube, limit):
    size = max(1, min(limit, 100))
    nodes, seen = [], set()
    total = None
    page = 1
    while True:
        data = await upstream.request(
            "list_profile_memories",
            {
                "mem_cube_id": cube,
                "page": page,
                "page_size": size,
            },
            code=200,
        )
        if not isinstance(data, dict) or data.get("cube_id") != cube:
            raise malformed()
        rows, count = data.get("memories"), data.get("total")
        if (
            not isinstance(rows, list)
            or type(count) is not int
            or count < 0
            or len(rows) != min(size, max(0, count - (page - 1) * size))
            or (total is not None and total != count)
        ):
            raise malformed()
        total = count
        for row in rows:
            node = profile_node(row, cube)
            if node["id"] in seen:
                raise malformed()
            seen.add(node["id"])
            if len(nodes) < limit:
                nodes.append(node)
        if len(nodes) >= min(limit, total):
            return nodes, total
        page += 1


async def profile_fulltext(upstream, cube, identifier):
    row = await upstream.request(
        "get_profile_memory",
        {
            "mem_cube_id": cube,
            "memory_id": identifier,
        },
        code=200,
    )
    if row is None:
        raise missing()
    node = profile_node(row, cube)
    if node["id"] != identifier:
        raise malformed()
    memory = row["memory"]
    if len(json.dumps({"memory": memory}, ensure_ascii=False).encode()) > 1024 * 1024:
        raise ApiError(41300, "Memory body exceeds the view limit", status_code=413)
    return {"id": identifier, "memory": memory}
