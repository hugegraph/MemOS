"""Portable, read-only live-service checks; no private fixtures or local setup imports."""

import argparse
import getpass
import json
import os
import re
from urllib.parse import quote, urlsplit

import httpx

TABS = ("text_mem", "pref_mem", "event_mem", "profile_mem", "skill_mem", "tool_mem")


def envelope(response, code=0):
    response.raise_for_status()
    body = response.json()
    if body.get("code") != code:
        raise ValueError("Unexpected response envelope")
    return body["data"]


def origin(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or (parsed.username or parsed.password or parsed.query or parsed.fragment)
    ):
        raise argparse.ArgumentTypeError("Use an HTTP(S) service URL without credentials")
    return value.rstrip("/")


def run_checks(web, base, project, native=None, cube=None):
    meta = envelope(web.get(base + "/api/meta"))
    assert meta["mode"] == "live"
    envelope(web.get(base + "/api/auth/me"))
    envelope(web.get(base + "/api/dashboard/project/dict"))
    proof = {"live": True, "project": project, "pages": [], "sameIdNativeMatches": 0}
    for tab in TABS:
        response = web.post(
            base + "/api/dashboard/project/memory/list",
            json={"projectId": project, "memoryDetailType": tab, "pageNum": 1, "pageSize": 10},
        )
        data = envelope(response)
        assert len(response.content) < 200000
        rows = data["memListItemResponseList"]
        for row in rows:
            assert {"memoryId", "memoryContent", "createTime"} <= row.keys()
            assert not {"embedding", "sources", "history", "metadata"} & row.keys()
        proof["pages"].append({"tab": tab, "total": data["total"], "bytes": len(response.content)})
        if native and rows:
            identifier = rows[0]["memoryId"]
            full = envelope(
                web.get(
                    base + "/api/dashboard/graph/memory",
                    params={
                        "tenant": project,
                        "id": identifier,
                    },
                )
            )
            if tab == "profile_mem":
                remote = envelope(
                    web.post(
                        native + "/product/get_profile_memory",
                        json={
                            "mem_cube_id": cube,
                            "memory_id": identifier,
                        },
                    ),
                    code=200,
                )
            else:
                remote = envelope(
                    web.get(
                        native + "/product/get_memory/" + quote(identifier, safe=""),
                        params={"mem_cube_id": cube},
                    ),
                    code=200,
                )
            assert remote["id"] == identifier == full["id"]
            assert remote["memory"] == full["memory"]
            proof["sameIdNativeMatches"] += 1
    graph = envelope(web.get(base + "/api/dashboard/graph/memories", params={"tenant": project}))
    proof["graphNodes"] = len(graph["nodes"])
    proof["graphTruncated"] = graph["stats"]["truncated"]
    proof["models"] = envelope(web.get(base + "/api/dashboard/models"))["total"]
    logs = envelope(
        web.post(
            base + "/api/dashboard/log/list",
            json={
                "projectId": project,
                "pageNum": 1,
                "pageSize": 10,
            },
        )
    )
    proof["logTotal"] = logs["total"]
    envelope(web.post(base + "/api/dashboard/usage/query", json={"projectId": project}))
    envelope(web.post(base + "/api/dashboard/prompt-config/detail", json={"projectId": project}))
    proof["passed"] = True
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=origin, default="http://127.0.0.1:5281")
    parser.add_argument("--project", required=True)
    parser.add_argument("--native", type=origin)
    parser.add_argument("--cube")
    args = parser.parse_args()
    if bool(args.native) != bool(args.cube):
        parser.error("--native and --cube must be supplied together")
    username = os.getenv("DASHBOARD_TEST_USERNAME") or input("Username: ")
    password = os.getenv("DASHBOARD_TEST_PASSWORD") or getpass.getpass("Password: ")
    try:
        with httpx.Client(trust_env=False, timeout=45, follow_redirects=False) as web:
            page = web.get(args.base + "/__login")
            page.raise_for_status()
            token = re.search(r'name="csrf" value="([^\"]+)"', page.text)
            assert token is not None
            response = web.post(
                args.base + "/__login",
                data={
                    "username": username,
                    "password": password,
                    "csrf": token[1],
                },
            )
            assert response.status_code == 303
            print(json.dumps(run_checks(web, args.base, args.project, args.native, args.cube)))
        return 0
    except Exception as error:
        # Do not echo service bodies, auth headers, credentials or business memories.
        print(json.dumps({"passed": False, "errorType": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
