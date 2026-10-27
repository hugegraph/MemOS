"""Final local HTTP/contract and reversible synthetic lifecycle acceptance, with Mock stopped."""

import hashlib
import json
import socket
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator
from server.backend.app import create_app

ROOT = Path("D:/MemOS")
BASE = "http://127.0.0.1:5274"
NATIVE = "http://127.0.0.1:8000"


def main():
    sys.path.insert(0, str(ROOT / "setup"))
    from verify_task01 import authenticate_local_backend

    spec = json.loads((ROOT / "dashboard/openapi.json").read_text(encoding="utf-8"))
    evidence = ROOT / "dashboard/.data/task16"
    evidence.mkdir(exist_ok=True)
    run = uuid.uuid4().hex[:12]
    checks = []

    def record(name, **details):
        checks.append({"name": name, "passed": True, **details})

    def validate(response, component):
        assert response.status_code == 200, (component, response.status_code)
        payload = response.json()
        assert payload["code"] == 0
        if component in spec["components"]["responses"]:
            schema = spec["components"]["responses"][component]["content"]["application/json"][
                "schema"
            ]
        else:
            schema = {
                "allOf": [
                    {"$ref": "#/components/schemas/Envelope"},
                    {"properties": {"data": {"$ref": "#/components/schemas/" + component}}},
                ]
            }
        Draft202012Validator({**schema, "components": spec["components"]}).validate(payload)
        return payload["data"]

    def post(web, path, body, component):
        return validate(web.post(BASE + path, json=body), component)

    try:
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", 5280)) != 0, "Mock must be stopped"
        record("mock_port_closed")
        app = create_app()
        routes = {
            (method.lower(), route.path)
            for route in app.routes
            for method in getattr(route, "methods", [])
        }
        fixed = {
            "/api/dashboard/skill/skillMD",
            "/api/user/quota/summary",
            "/api/user/subscription/details",
        }
        matrix = []
        for path, operations in spec["paths"].items():
            for method in operations:
                if method not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                    continue
                implemented = (method, path) in routes
                status = (
                    "fixed_compatibility"
                    if path in fixed
                    else "frozen_knowledge"
                    if "/knowledgebase/" in path
                    else "implemented"
                )
                assert implemented or (method, path) == (
                    "post",
                    "/api/dashboard/knowledgebase/memory/list",
                ), (method, path)
                matrix.append(
                    {
                        "method": method.upper(),
                        "path": path,
                        "status": status,
                        "handler_registered": implemented,
                    }
                )
        (evidence / "operation-matrix.json").write_text(
            json.dumps(matrix, indent=2), encoding="utf-8"
        )
        record(
            "all_spec_operations_classified",
            operations=len(matrix),
            implemented=sum(row["handler_registered"] for row in matrix),
            fixed_compatibility=sum(row["status"] == "fixed_compatibility" for row in matrix),
        )
        with httpx.Client(trust_env=False, timeout=45) as web:
            meta = validate(web.get(BASE + "/api/meta"), "Meta")
            assert meta["mode"] == "live"
            quota = validate(web.get(BASE + "/api/user/quota/summary"), "QuotaSummary")
            assert quota["memoryCount"] == 0
            subscription = post(
                web,
                "/api/user/subscription/details",
                {
                    "cycleId": "",
                    "includeAllCycles": True,
                },
                "SubscriptionDetails",
            )
            assert subscription == {"cycles": []}
            assert web.post(BASE + "/api/dashboard/project/list", json={}).status_code == 401
            record("main_frontend_live_and_anonymous_denied")
            authenticate_local_backend(web)
            assert (
                post(
                    web,
                    "/api/dashboard/skill/skillMD",
                    {
                        "oss_url": "https://example.invalid/not-fetched",
                    },
                    "StringData",
                )
                == ""
            )
            record("fixed_compatibility_contracts")
            validate(web.get(BASE + "/api/auth/me"), "Account")
            validate(web.get(BASE + "/api/dashboard/project/dict"), "ProjectDict")
            post(web, "/api/dashboard/project/list", {"pageSize": 200}, "ProjectRecords")
            validate(web.get(BASE + "/admin/accounts"), "AdminAccountRecords")
            project = "issue3-project-a"
            post(
                web,
                "/api/dashboard/project/update",
                {"id": project, "status": "ENABLED"},
                "Project",
            )
            record("project_account_session_contracts")
            for tab in [
                "text_mem",
                "pref_mem",
                "event_mem",
                "profile_mem",
                "skill_mem",
                "tool_mem",
            ]:
                response = web.post(
                    BASE + "/api/dashboard/project/memory/list",
                    json={"projectId": project, "memoryDetailType": tab, "pageSize": 10},
                )
                validate(response, "MemoryList")
                assert len(response.content) < 200 * 1024
                record("six_tab_projection", tab=tab, bytes=len(response.content))
            seed = json.loads((ROOT / ".local/task03-seed.json").read_text(encoding="utf-8"))
            assert seed["marker"].startswith("Task03-")
            memory = seed["memory_id"]
            before = post(
                web,
                "/api/dashboard/project/memory/detail",
                {"memoryId": memory, "memType": "text_mem"},
                "Memory",
            )
            native = web.get(
                NATIVE + "/product/get_memory/" + memory, params={"mem_cube_id": project}
            ).json()["data"]
            assert native["id"] == before["memoryId"] == memory
            assert before["memoryContent"] == native["memory"]
            record(
                "unique_synthetic_same_id_rest",
                memory_id=memory,
                content_sha256=hashlib.sha256(before["memoryContent"].encode()).hexdigest(),
            )
            for identifier, tab in [
                (memory, "text_mem"),
                (seed["profile_field_ids"][0], "profile_mem"),
            ]:
                original = post(
                    web,
                    "/api/dashboard/project/memory/detail",
                    {"memoryId": identifier, "memType": tab},
                    "Memory",
                )
                body = {"projectId": project, "memory_ids": [identifier], "memType": tab}
                try:
                    deleted = post(
                        web,
                        "/api/dashboard/project/memory/delete",
                        {**body, "operationId": "task16-delete-" + run + tab},
                        "MemoryOperation",
                    )
                    assert deleted["state"] == "confirmed" and deleted["deleted"] == 1
                    assert (
                        web.post(
                            BASE + "/api/dashboard/project/memory/detail",
                            json={"memoryId": identifier, "memType": tab},
                        ).status_code
                        == 404
                    )
                    if tab == "text_mem":
                        assert (
                            web.get(
                                NATIVE + "/product/get_memory/" + identifier,
                                params={"mem_cube_id": project},
                            ).json()["data"]
                            is None
                        )
                        subprocess.run(
                            [sys.executable, str(ROOT / "setup/task03_fault_probe.py"), "read"],
                            check=True,
                            capture_output=True,
                            timeout=40,
                        )
                        raw = json.loads(
                            (ROOT / ".local/task03-fault-read.json").read_text(encoding="utf-8")
                        )
                        assert raw["graph_status"] == raw["vector_status"] == "deleted"
                        assert raw["stale_vector_result_count"] == 0
                        record("independent_graph_vector_deleted", stale_vector_results=0)
                finally:
                    restored = post(
                        web,
                        "/api/dashboard/project/memory/restore",
                        {**body, "operationId": "task16-restore-" + run + tab},
                        "MemoryOperation",
                    )
                    assert restored["state"] == "confirmed" and restored["restored"] == 1
                    final = post(
                        web,
                        "/api/dashboard/project/memory/detail",
                        {"memoryId": identifier, "memType": tab},
                        "Memory",
                    )
                    assert final["memoryContent"] == original["memoryContent"]
                record("real_soft_delete_restore", tab=tab, same_id=True, same_content=True)
            logs = post(
                web, "/api/dashboard/log/list", {"projectId": project, "scope": "all"}, "LogList"
            )
            assert logs["logs"]
            log = post(
                web, "/api/dashboard/log/detail", {"logId": logs["logs"][0]["id"]}, "LogDetail"
            )
            assert isinstance(log["requestBody"], str) and isinstance(log["responseBody"], str)
            json.loads(log["requestBody"])
            json.loads(log["responseBody"])
            record("audit_list_detail_json_strings")
            post(web, "/api/dashboard/usage/query", {"projectId": project}, "UsageQuery")
            post(web, "/api/dashboard/prompt-config/detail", {"projectId": project}, "PromptConfig")
            post(
                web,
                "/api/dashboard/profileTemplate/list",
                {"projectId": project},
                "ProfileTemplateList",
            )
            validate(
                web.get(BASE + "/api/dashboard/graph/memories", params={"tenant": project}),
                "GraphMemoryGraph",
            )
            validate(web.get(BASE + "/api/dashboard/models"), "ModelRecords")
            record("usage_prompt_templates_graph_models_contracts")
            foreign = post(
                web,
                "/api/dashboard/project/memory/list",
                {
                    "projectId": "issue3-project-b",
                    "memoryDetailType": "text_mem",
                    "memSearchContent": seed["marker"],
                },
                "MemoryList",
            )
            assert foreign["total"] == 0
            record("foreign_project_unique_marker_absent")
        result = {"passed": True, "run_id": run, "checks": checks}
    except Exception as error:
        result = {
            "passed": False,
            "run_id": run,
            "checks": checks,
            "error_type": type(error).__name__,
        }
        raise
    finally:
        serialized = json.dumps(result, indent=2)
        assert "sk-" not in serialized
        (evidence / "live.json").write_text(serialized, encoding="utf-8")
        print(json.dumps({"passed": result["passed"], "checks": len(checks)}))


if __name__ == "__main__":
    main()
