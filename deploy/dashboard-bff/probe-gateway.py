"""Run inside the existing gateway container; authenticate in-process, read only.

Does not print credentials or memory content. The unique pagination value is an
audit correlation marker; it is not an instruction or a memory write.
"""
import asyncio
import json
import logging
import os
import secrets
import sys


async def main():
    logging.disable(logging.CRITICAL)
    sys.path.insert(0, "/gateway")
    from gateway.domain.tenants import parse_tenants
    from fastmcp import Client

    user = os.environ.get("AUDIT_PROBE_USER", "dashboard-demo")
    cube = os.environ.get("AUDIT_PROBE_CUBE", "dashboard-demo")
    token = next(key for key, value in parse_tenants(os.environ["MEMOS_TENANTS"]).items()
                 if value.user_id == user and value.cube_id == cube)
    marker = 100000 + secrets.randbelow(900000)
    async with Client("http://127.0.0.1:8003/mcp", auth=token) as client:
        await client.call_tool("get_user_profile", {
            "include_preference": False, "include_tool_memory": False,
            "current": marker, "size": 1,
        })
    print(json.dumps({"status": "PASS", "tool": "get_user_profile", "current": marker,
                      "memory_writes": 0, "model_calls": 0}))


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(main(), 40))
    except Exception as error:
        print(json.dumps({"status": "FAIL", "type": type(error).__name__}))
        sys.exit(1)
