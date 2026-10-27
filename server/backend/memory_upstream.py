"""Bounded, typed reads of MemOS REST; no model SDK or database coupling."""

import asyncio
import json
import re
from urllib.parse import quote

import httpx

from .errors import ApiError
from .memory_models import TAB_TYPES, TAB_VIEWS, MemoryListRequest, build_filter, time_boundary

MAX_RESPONSE_BYTES = 32 * 1024 * 1024


def bad_upstream() -> ApiError:
    return ApiError(50200, "上游记忆响应格式或业务结果不符合约定", status_code=502)


class MemoryUpstream:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def request(self, method: str, path: str, *, body=None, params=None, allow_failure=False):
        try:
            async with asyncio.timeout(self.client.timeout.read):
                async with self.client.stream(method, path, json=body, params=params) as response:
                    if response.status_code != 200 or response.headers.get(
                        "content-encoding", "identity"
                    ).strip().lower() not in {"", "identity"}:
                        raise bad_upstream()
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise bad_upstream()
                        raw.extend(chunk)
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ApiError(50400, "上游记忆查询超时", status_code=504) from exc
        except httpx.ProtocolError as exc:
            raise bad_upstream() from exc
        except httpx.RequestError as exc:
            raise ApiError(50300, "无法连接上游记忆服务", status_code=503) from exc
        try:
            envelope = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise bad_upstream() from exc
        if not isinstance(envelope, dict) or type(envelope.get("code")) is not int:
            raise bad_upstream()
        if envelope["code"] != 200:
            if allow_failure and envelope["code"] in (400, 403, 404, 409):
                return {"error_code": envelope["code"], "success": False}
            raise bad_upstream()
        data = envelope.get("data")
        if (
            not allow_failure
            and isinstance(data, dict)
            and (
                data.get("status") in ("failure", "failed", "error") or data.get("success") is False
            )
        ):
            raise bad_upstream()
        return data

    async def page(self, cube: str, request: MemoryListRequest) -> tuple[list[dict], int]:
        tab = request.memoryDetailType
        if tab == "profile_mem":
            start, _ = time_boundary(request.startTime)
            end, exclusive = time_boundary(request.endTime, end=True)
            data = await self.request(
                "POST",
                "product/list_profile_memories",
                body={
                    "mem_cube_id": cube,
                    "subject_type": request.subjectType,
                    "subject_id": request.externalUserId,
                    "search": request.memSearchContent,
                    "knowledgebase_id": request.knowledgeBaseId,
                    "start_time": start.isoformat() if start else None,
                    "end_time": end.isoformat() if end else None,
                    "end_exclusive": exclusive,
                    "page": request.pageNum,
                    "page_size": request.pageSize,
                },
            )
            if not isinstance(data, dict) or data.get("cube_id") != cube:
                raise bad_upstream()
            rows, total = data.get("memories"), data.get("total")
        else:
            body = {
                "mem_cube_id": cube,
                "include_memory_view": [TAB_VIEWS[tab]],
                "status": ["activated"],
                "include_sources": False,
                "page": request.pageNum,
                "page_size": request.pageSize,
                "filter": build_filter(request),
            }
            if tab == "event_mem":
                body["event_memory_types"] = list(TAB_TYPES[tab])
            data = await self.request("POST", "product/get_memory", body=body)
            buckets = data.get(tab) if isinstance(data, dict) else None
            if (
                not isinstance(buckets, list)
                or len(buckets) != 1
                or not isinstance(buckets[0], dict)
                or buckets[0].get("cube_id") != cube
            ):
                raise bad_upstream()
            rows, total = buckets[0].get("memories"), buckets[0].get("total_nodes")
        if (
            not isinstance(rows, list)
            or not all(isinstance(row, dict) for row in rows)
            or type(total) is not int
            or total < len(rows)
            or len(rows) > request.pageSize
        ):
            raise bad_upstream()
        return rows, total

    async def detail(self, cube: str, identifier: str, tab: str) -> dict | None:
        if tab == "profile_mem":
            data = await self.request(
                "POST",
                "product/get_profile_memory",
                body={"mem_cube_id": cube, "memory_id": identifier},
            )
        else:
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", identifier):
                raise ApiError(40000, "memoryId 格式不合法", status_code=400)
            data = await self.request(
                "GET",
                "product/get_memory/" + quote(identifier, safe=""),
                params={"mem_cube_id": cube, "include_sources": "false"},
            )
        if data is not None and (not isinstance(data, dict) or data.get("id") != identifier):
            raise bad_upstream()
        return data
