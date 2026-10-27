"""Read-only MemOS connectivity probe with strict protocol validation."""

import asyncio
import json

import httpx
from pydantic import ValidationError

from .errors import ApiError
from .models import UpstreamHealth


class UpstreamClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def health(self) -> UpstreamHealth:
        try:
            async with asyncio.timeout(self.client.timeout.read):
                async with self.client.stream("GET", "health") as response:
                    if response.status_code != 200:
                        raise ApiError(
                            50200, "上游 MemOS 健康检查返回异常 HTTP 状态", status_code=502
                        )
                    if response.headers.get("content-encoding", "identity").strip().lower() not in {
                        "",
                        "identity",
                    }:
                        raise ApiError(
                            50200, "上游 MemOS 健康检查返回不支持的内容编码", status_code=502
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > 65536:
                            raise ApiError(50200, "上游 MemOS 健康检查响应过大", status_code=502)
                        body.extend(chunk)
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise ApiError(50400, "上游 MemOS 健康检查超时", status_code=504) from exc
        except httpx.ProtocolError as exc:
            raise ApiError(50200, "上游 MemOS 返回异常 HTTP 协议", status_code=502) from exc
        except httpx.RequestError as exc:
            raise ApiError(50300, "无法连接上游 MemOS 服务", status_code=503) from exc

        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise ApiError(50200, "上游 MemOS 健康检查返回无效 JSON", status_code=502) from exc
        if not isinstance(payload, dict):
            raise ApiError(50200, "上游 MemOS 健康检查响应格式不匹配", status_code=502)
        if payload.get("status") not in (None, "healthy"):
            raise ApiError(50300, "上游 MemOS 当前不健康", status_code=503)
        try:
            return UpstreamHealth.model_validate(payload)
        except ValidationError as exc:
            raise ApiError(50200, "上游 MemOS 服务身份或契约不匹配", status_code=502) from exc
