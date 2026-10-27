"""Bounded upstream product transport; success codes differ by product API family."""

import asyncio
import json

import httpx

from .errors import ApiError


def malformed():
    return ApiError(50200, "上游响应不符合约定", status_code=502)


class ProductClient:
    def __init__(self, client):
        self.client = client

    async def raw(self, path, body=None, *, method="POST", limit=32 * 1024 * 1024, timeout=None):
        seconds = timeout or self.client.timeout.read
        try:
            async with asyncio.timeout(seconds):
                async with self.client.stream(
                    method, "product/" + path, json=body, timeout=seconds
                ) as response:
                    if response.status_code in {400, 403, 404, 409, 413, 422}:
                        status = 400 if response.status_code == 422 else response.status_code
                        raise ApiError(
                            {400: 40000, 403: 40003, 404: 40001, 409: 40900, 413: 41300}[status],
                            "上游拒绝该操作",
                            status_code=status,
                        )
                    if response.status_code == 503:
                        raise ApiError(50300, "上游暂不可用", status_code=503)
                    if response.status_code != 200 or response.headers.get(
                        "content-encoding", "identity"
                    ).lower() not in {"", "identity"}:
                        raise malformed()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > limit:
                            raise malformed()
                        data.extend(chunk)
                    return bytes(data)
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ApiError(50400, "上游操作超时", status_code=504) from exc
        except httpx.ProtocolError as exc:
            raise malformed() from exc
        except httpx.RequestError as exc:
            raise ApiError(50300, "无法连接上游", status_code=503) from exc

    async def request(self, path, body=None, *, code=0, method="POST", timeout=None):
        raw = await self.raw(path, body, method=method, timeout=timeout)
        try:
            value = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise malformed() from exc
        if not isinstance(value, dict) or type(value.get("code")) is not int:
            raise malformed()
        result_code = value["code"]
        if result_code != code:
            if result_code in {400, 403, 404, 409}:
                raise ApiError(
                    {400: 40000, 403: 40003, 404: 40001, 409: 40900}[result_code],
                    "上游拒绝该操作",
                    status_code=result_code,
                )
            raise malformed()
        data = value.get("data")
        if isinstance(data, dict) and (
            data.get("success") is False
            or (
                isinstance(data.get("status"), str)
                and data["status"] in {"failed", "failure", "error"}
            )
        ):
            raise malformed()
        return data
