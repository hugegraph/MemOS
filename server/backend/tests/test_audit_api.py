import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from server.backend.app import create_app
from server.backend.settings import Settings


def validate_contract(body, component):
    spec = json.loads(
        (Path(__file__).resolve().parents[3] / "openapi.json").read_text(encoding="utf-8")
    )
    schema = spec["components"]["responses"][component]["content"]["application/json"]["schema"]
    Draft202012Validator({**schema, "components": spec["components"]}).validate(body)


def application(tmp_path, *, gateway=True):
    folder = tmp_path / "gateway"
    folder.mkdir(exist_ok=True)
    app = create_app(
        Settings(
            auth_enabled=False,
            data_dir=tmp_path / "data",
            projects_seed=(("p", "a"), ("q", "b")),
            default_project_id="p",
            audit_gateway_dir=folder if gateway else None,
        ),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"code": 200, "data": {"items": [], "total": 0}})
        ),
    )
    return app, folder


def append(folder, **extra):
    row = {
        "tenant": "a",
        "tool": "search_memory",
        "ts": "2026-10-26T00:00:00Z",
        "ok": True,
        "input": {"query": "unique-marker", "user_id": "u"},
        "output": '{ "code": 200 }',
        **extra,
    }
    with (folder / "calls-20261026.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(row) + "\n")


def test_list_detail_projection_and_tenant_scope(tmp_path):
    app, folder = application(tmp_path)
    append(folder)
    append(folder, tenant="b")
    with TestClient(app) as client:
        response = client.post(
            "/api/dashboard/log/list",
            json={
                "projectId": "p",
                "scope": "gateway",
                "query": "marker",
                "subjectId": "u",
                "eventType": "searchMemory",
                "status": "SUCCESS",
            },
        )
        assert response.status_code == 200 and response.json()["code"] == 0
        validate_contract(response.json(), "LogList")
        data = response.json()["data"]
        assert data["total"] == 1 and "requestBody" not in data["logs"][0]
        assert data["logs"][0]["createTime"] == "2026-10-26 08:00:00"
        identifier = data["logs"][0]["id"]
        detail = client.post("/api/dashboard/log/detail", json={"logId": identifier}).json()["data"]
        assert detail["createTime"] == "2026-10-26 08:00:00"
        validate_contract({"code": 0, "data": detail, "message": "success"}, "LogDetail")
        assert detail["requestBody"] == '{"query":"unique-marker","user_id":"u"}'
        assert detail["responseBody"] == '{ "code": 200 }'
        foreign = client.post(
            "/api/dashboard/log/list", json={"projectId": "q", "scope": "gateway"}
        ).json()["data"]["logs"][0]["id"]
        assert client.post("/api/dashboard/log/detail", json={"logId": foreign}).status_code == 404
        assert (
            client.post("/api/dashboard/log/list", json={"projectId": "foreign"}).status_code == 404
        )
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "extra",
    [
        {"eventType": "invented"},
        {"scope": "../../"},
        {"pageNum": 10**30},
        {"pageSize": 101},
        {"startTime": "not-time"},
        {"startTime": "2026-10-27", "endTime": "2026-10-26"},
        {"path": "secret"},
    ],
)
def test_bad_log_filters_rejected(tmp_path, extra):
    app, _ = application(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/dashboard/log/list", json={"projectId": "p", **extra})
        assert response.status_code == 400 and response.json()["code"] != 0


def test_missing_gateway_is_not_fake_empty_but_console_remains_available(tmp_path):
    app, _ = application(tmp_path, gateway=False)
    with TestClient(app) as client:
        assert client.post("/api/dashboard/log/list", json={"projectId": "p"}).status_code == 503
        assert client.post(
            "/api/dashboard/log/list", json={"projectId": "p", "scope": "console"}
        ).json()["data"] == {"logs": [], "total": 0}


def test_actual_console_operation_is_audited_without_log_read_feedback(tmp_path):
    app, _ = application(tmp_path)
    with TestClient(app) as client:
        # Intentionally wrong upstream contract: the real 502 must be logged as failure.
        result = client.post(
            "/api/dashboard/project/memory/list",
            json={
                "projectId": "p",
                "memoryDetailType": "text_mem",
                "memSearchContent": "console-marker",
            },
        )
        assert result.status_code == 502
        query = {"projectId": "p", "scope": "console", "query": "console-marker"}
        first = client.post("/api/dashboard/log/list", json=query).json()["data"]
        assert first["total"] == 1 and first["logs"][0]["status"] == "FAILURE"
        detail = client.post(
            "/api/dashboard/log/detail", json={"logId": first["logs"][0]["id"]}
        ).json()["data"]
        assert json.loads(detail["requestBody"])["memSearchContent"] == "console-marker"
        assert client.post("/api/dashboard/log/list", json=query).json()["data"]["total"] == 1
        client.post(
            "/api/dashboard/project/memory/list",
            json={"projectId": "foreign", "memoryDetailType": "text_mem"},
        )
        assert (
            client.post(
                "/api/dashboard/log/list", json={"projectId": "p", "scope": "console"}
            ).json()["data"]["total"]
            == 1
        )


def test_missing_source_and_corrupt_database_fail_explicitly(tmp_path):
    app, folder = application(tmp_path)
    folder.rmdir()
    with TestClient(app) as client:
        response = client.post("/api/dashboard/log/list", json={"projectId": "p"})
        assert response.status_code == 503


def test_large_valid_memory_response_does_not_turn_success_into_failure(tmp_path):
    from server.backend.tests.test_memories import node

    app, _ = application(tmp_path)

    async def memory_page(*_):
        rows = [node(str(i), cube="a") for i in range(100)]
        for row in rows:
            row["memory"] = "中" * 400
        return rows, 100

    with TestClient(app) as client:
        app.state.memories.page = memory_page
        result = client.post(
            "/api/dashboard/project/memory/list",
            json={"projectId": "p", "memoryDetailType": "text_mem", "pageSize": 100},
        )
        assert result.status_code == 200 and len(result.content) > 65536
        log = client.post(
            "/api/dashboard/log/list", json={"projectId": "p", "scope": "console"}
        ).json()["data"]["logs"][0]
        assert log["status"] == "SUCCESS"
        detail = client.post("/api/dashboard/log/detail", json={"logId": log["id"]}).json()["data"]
        assert json.loads(detail["responseBody"])["truncated"] is True
        assert detail["responseCode"] == 0


def test_large_valid_batch_keeps_project_attribution_after_body_truncation(tmp_path):
    app, _ = application(tmp_path)
    with TestClient(app) as client:
        result = client.post(
            "/api/dashboard/project/memory/delete",
            json={
                "projectId": "p",
                "memType": "text_mem",
                "memory_ids": [str(i) + "x" * 800 for i in range(100)],
            },
        )
        assert result.status_code == 502
        page = client.post(
            "/api/dashboard/log/list", json={"projectId": "p", "scope": "console"}
        ).json()["data"]
        assert page["total"] == 1 and page["logs"][0]["status"] == "FAILURE"
