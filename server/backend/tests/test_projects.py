import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient
from server.backend.app import create_app
from server.backend.settings import Settings

BASE = "/api/dashboard/project/"


def app_for(tmp_path):
    gateway = tmp_path / "gateway"
    gateway.mkdir(exist_ok=True)
    return create_app(
        Settings(
            auth_enabled=False,
            data_dir=tmp_path / "data",
            projects_seed=(("a", "cube-a"), ("b", "cube-b")),
            default_project_id="a",
            audit_gateway_dir=gateway,
        ),
        transport=httpx.MockTransport(lambda req: httpx.Response(500)),
    )


def post(web, endpoint, **body):
    response = web.post(BASE + endpoint, json=body)
    assert response.status_code == 200, (endpoint, response.status_code, response.text)
    assert response.json()["code"] == 0
    return response.json()["data"]


def create(web, name="new project", **extra):
    return post(web, "create", name=name, description="description", isMultiView=1, **extra)


def test_real_create_edit_restart_selection_and_mapping(tmp_path):
    with TestClient(app_for(tmp_path)) as web:
        created = create(web)
        identifier = created["id"]
        assert created["name"] == "new project" and created["isMultiView"] == 1
        assert created["status"] == "DISABLED" and created["disabled"] is False
        assert web.app.state.control.resolve_cube(identifier) == identifier
        edited = post(web, "update", id=identifier, name="changed", description="updated")
        assert edited["isMultiView"] == 1 and edited["description"] == "updated"
        post(web, "update", id=identifier, status="ENABLED")
        cookies = dict(web.cookies)
    with TestClient(app_for(tmp_path)) as web:
        web.cookies.update(cookies)
        rows = web.get(BASE + "dict").json()["data"]
        assert [r["id"] for r in rows if r["status"] == "ENABLED"] == [identifier]
        page = post(web, "list", pageNum=1, pageSize=200)
        assert page["total"] == 3
        assert next(r for r in page["records"] if r["id"] == identifier)["name"] == "changed"
        assert web.app.state.control.resolve_cube(identifier) == identifier


def test_two_browser_contexts_do_not_change_each_other(tmp_path):
    with TestClient(app_for(tmp_path)) as one, TestClient(app_for(tmp_path)) as two:
        one.get(BASE + "dict")
        two.get(BASE + "dict")
        post(one, "update", id="b", status="ENABLED")
        assert [
            r["id"] for r in one.get(BASE + "dict").json()["data"] if r["status"] == "ENABLED"
        ] == ["b"]
        assert [
            r["id"] for r in two.get(BASE + "dict").json()["data"] if r["status"] == "ENABLED"
        ] == ["a"]
        assert all(cookie.has_nonstandard_attr("HttpOnly") for cookie in one.cookies.jar)


def test_disable_archive_restore_and_restart_never_reseed_active(tmp_path):
    with TestClient(app_for(tmp_path)) as web:
        post(web, "update", id="b", status="ENABLED")
        stopped = post(web, "update", id="b", status="DISABLED")
        assert stopped["disabled"] is True
        assert (
            web.post(
                BASE + "memory/list", json={"projectId": "b", "memoryDetailType": "text_mem"}
            ).status_code
            == 404
        )
        assert web.post("/api/dashboard/log/list", json={"projectId": "b"}).status_code == 404
        assert web.post("/api/dashboard/usage/query", json={"projectId": "b"}).status_code == 404
    with TestClient(app_for(tmp_path)) as web:
        row = next(r for r in post(web, "list")["records"] if r["id"] == "b")
        assert row["disabled"] is True
        enabled = post(web, "update", id="b", status="ENABLED")
        assert enabled["disabled"] is False and enabled["status"] == "DISABLED"
        result = post(web, "delete/b")
        assert result == {"ok": True, "id": "b", "archived": True}
        assert "b" not in [r["id"] for r in web.get(BASE + "dict").json()["data"]]
        assert web.post(BASE + "update", json={"id": "b", "status": "ENABLED"}).status_code == 409
        restored = post(web, "restore/b")
        assert restored["archived"] is False and restored["status"] == "DISABLED"
        assert post(web, "revoke/b")["ok"] is True
        assert next(r for r in post(web, "list")["records"] if r["id"] == "b")["archived"] is True


def test_detail_resolves_selected_project_and_log_ids_remain_scoped(tmp_path):
    from server.backend.tests.test_memories import node

    rows = [
        {"tenant": cube, "tool": "search_memory", "ts": "2026-10-26T00:00:00Z", "ok": True}
        for cube in ("cube-a", "cube-b")
    ]
    app = app_for(tmp_path)
    (tmp_path / "gateway/calls-20261026.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )

    seen = []

    async def memory_detail(cube, identifier, tab):
        seen.append(cube)
        return node(identifier, cube=cube)

    with TestClient(app) as web:
        app.state.memories.detail = memory_detail
        a = web.post("/api/dashboard/log/list", json={"projectId": "a", "scope": "gateway"}).json()[
            "data"
        ]["logs"][0]["id"]
        b = web.post("/api/dashboard/log/list", json={"projectId": "b", "scope": "gateway"}).json()[
            "data"
        ]["logs"][0]["id"]
        post(web, "update", id="b", status="ENABLED")
        assert web.post("/api/dashboard/log/detail", json={"logId": a}).status_code == 404
        assert web.post("/api/dashboard/log/detail", json={"logId": b}).status_code == 200
        assert (
            web.post(
                BASE + "memory/detail", json={"memoryId": "one", "memType": "text_mem"}
            ).status_code
            == 200
        )
        assert seen == ["cube-b"]


@pytest.mark.parametrize(
    "endpoint,body",
    [
        ("create", {"name": " ", "description": "x"}),
        ("create", {"name": "x", "description": "x", "upstream_cube_id": "foreign"}),
        ("update", {"id": "a"}),
        ("update", {"id": "a", "name": None}),
        ("update", {"id": "a", "disabled": True}),
        ("list", {"pageSize": 201}),
        ("list", {"pageNum": False}),
    ],
)
def test_invalid_project_requests_fail(tmp_path, endpoint, body):
    with TestClient(app_for(tmp_path)) as web:
        result = web.post(BASE + endpoint, json=body)
        assert result.status_code == 400 and result.json()["code"] != 0


def test_uniqueness_and_origin_and_pagination(tmp_path):
    with TestClient(app_for(tmp_path)) as web:
        create(web, "same")
        assert (
            web.post(BASE + "create", json={"name": " SAME ", "description": ""}).status_code == 409
        )
        assert web.post(BASE + "update", json={"id": "a", "name": "same"}).status_code == 409
        assert (
            web.post(
                BASE + "create",
                json={"name": "cross-site", "description": ""},
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        first = post(web, "list", pageSize=1)
        second = post(web, "list", pageSize=1, pageNum=2)
        assert (
            first["total"] == second["total"] == 3
            and first["records"][0]["id"] != second["records"][0]["id"]
        )


def test_concurrent_duplicate_create_is_atomic(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app) as web:

        def action(_):
            return web.post(
                BASE + "create", json={"name": "concurrent", "description": ""}
            ).status_code

        with ThreadPoolExecutor(max_workers=3) as executor:
            assert sorted(executor.map(action, range(3))) == [200, 409, 409]


def test_legacy_four_column_database_migrates_without_losing_mapping(tmp_path):
    import sqlite3

    folder = tmp_path / "data"
    folder.mkdir()
    with sqlite3.connect(folder / "control.db") as db:
        db.execute(
            "CREATE TABLE projects (id TEXT PRIMARY KEY,name TEXT NOT NULL,"
            "upstream_cube_id TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL)"
        )
        db.execute("INSERT INTO projects VALUES ('a','original','cube-a','2026-01-01T00:00:00Z')")
    with TestClient(app_for(tmp_path)) as web:
        page = post(web, "list")
        original = next(r for r in page["records"] if r["id"] == "a")
        assert original["name"] == "original" and original["createTime"] == "2026-01-01 08:00:00"
        assert web.app.state.control.resolve_cube("a") == "cube-a"


def test_last_project_can_be_archived_without_fabricating_current_project(tmp_path):
    with TestClient(app_for(tmp_path)) as web:
        post(web, "delete/a")
        post(web, "delete/b")
        assert web.get(BASE + "dict").json()["data"] == []
        assert (
            web.post(
                BASE + "memory/detail", json={"memoryId": "one", "memType": "text_mem"}
            ).status_code
            == 503
        )
        assert post(web, "restore/a")["archived"] is False


def test_partial_update_rolls_back_name_when_uniqueness_fails(tmp_path):
    with TestClient(app_for(tmp_path)) as web:
        result = web.post(
            BASE + "update", json={"id": "a", "name": "b", "description": "should-not-save"}
        )
        assert result.status_code == 409
        row = next(r for r in post(web, "list")["records"] if r["id"] == "a")
        assert row["name"] == "a" and row["description"] == ""


def test_project_lifecycle_responses_match_authoritative_openapi(tmp_path):
    from server.backend.tests.test_audit_api import validate_contract

    with TestClient(app_for(tmp_path)) as web:
        validate_contract(web.get(BASE + "dict").json(), "ProjectDict")
        validate_contract(web.post(BASE + "list", json={}).json(), "ProjectRecords")
        created = web.post(BASE + "create", json={"name": "contract", "description": ""})
        validate_contract(created.json(), "Project")
        identifier = created.json()["data"]["id"]
        updated = web.post(BASE + "update", json={"id": identifier, "status": "ENABLED"})
        validate_contract(updated.json(), "Project")
        validate_contract(web.post(BASE + "delete/" + identifier).json(), "ProjectArchive")
        validate_contract(web.post(BASE + "restore/" + identifier).json(), "Project")
        validate_contract(web.post(BASE + "revoke/" + identifier).json(), "Ok")
