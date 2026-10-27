"""Behavioral read-only tests; the upstream boundary uses its actual REST shapes."""

import json
import re
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from server.backend.app import create_app
from server.backend.settings import Settings

TAB_TYPES = {
    "text_mem": ["WorkingMemory", "LongTermMemory", "UserMemory", "OuterMemory"],
    "pref_mem": ["PreferenceMemory"],
    "event_mem": ["EventMemory", "Context", "InsightMemory"],
    "skill_mem": ["SkillMemory"],
    "tool_mem": ["ToolSchemaMemory", "ToolTrajectoryMemory"],
}
VIEWS = {
    "detail_factual": "text_mem",
    "preference": "pref_mem",
    "event": "event_mem",
    "skill": "skill_mem",
    "tool_memory": "tool_mem",
}
PROFILE_ID = "profile_" + "a" * 32 + "_basic.user_name_中文"


def node(identifier, kind="UserMemory", cube="cube-a", day=1):
    return {
        "id": identifier,
        "memory": "Marker " + identifier,
        "metadata": {
            "memory_type": kind,
            "status": "activated",
            "user_name": cube,
            "owner_type": "user",
            "owner_id": "u1",
            "user_id": "u1",
            "created_at": f"2026-01-{day:02d}T00:00:00+00:00",
            "embedding": [0.125] * 1024,
            "sources": ["x" * 50000],
            "history": ["y" * 50000],
        },
    }


def match(node, condition):
    if not condition:
        return True
    if "and" in condition:
        return all(match(node, part) for part in condition["and"])
    if "or" in condition:
        return any(match(node, part) for part in condition["or"])
    for key, value in condition.items():
        actual = node.get(key, node.get("metadata", {}).get(key))
        if isinstance(value, dict):
            for op, expected in value.items():
                if actual is None:
                    return False
                if op == "like" and expected not in actual:
                    return False
                if op == "contains" and expected not in actual:
                    return False
                if op in {"gte", "lte", "lt"}:
                    a = datetime.fromisoformat(actual.replace("Z", "+00:00"))
                    b = datetime.fromisoformat(expected.replace("Z", "+00:00"))
                    if not {"gte": a >= b, "lte": a <= b, "lt": a < b}[op]:
                        return False
        elif actual != value:
            return False
    return True


class Upstream:
    def __init__(self):
        self.calls = []
        self.nodes = [node(f"text-{i:02d}", day=i + 1) for i in range(25)]
        self.nodes[0]["metadata"]["knowledgebase_id"] = "kb-a"
        self.nodes[1]["metadata"]["knowledgebase_ids"] = ["kb-array"]
        for tab, kinds in TAB_TYPES.items():
            if tab != "text_mem":
                self.nodes.extend(node(kind, kind) for kind in kinds)
        self.nodes.append(node("foreign", cube="cube-b"))
        self.profile = {
            "id": PROFILE_ID,
            "memory": "basic.user_name_中文: Marker",
            "memory_type": "ProfileMemory",
            "status": "activated",
            "user_name": "cube-a",
            "subject_type": "user",
            "subject_id": "u1",
            "template_id": "template-real",
            "profile_instance_id": "profile_" + "a" * 32,
            "created_at": "2026-01-01T00:00:00Z",
        }

    def __call__(self, request):
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request, body))
        cube = body.get("mem_cube_id") or request.url.params.get("mem_cube_id")
        if request.url.path.endswith("/get_profile_memory"):
            data = self.profile if cube == "cube-a" and body["memory_id"] == PROFILE_ID else None
        elif request.url.path.endswith("/list_profile_memories"):
            rows = [self.profile] if cube == "cube-a" else []
            data = {"cube_id": cube, "memories": rows, "total": len(rows)}
        elif request.method == "GET":
            data = next(
                (
                    n
                    for n in self.nodes
                    if n["id"] == request.url.path.rsplit("/", 1)[-1]
                    and n["metadata"]["user_name"] == cube
                ),
                None,
            )
        else:
            tab = VIEWS[body["include_memory_view"][0]]
            types = body.get("event_memory_types", TAB_TYPES[tab])
            assert body["status"] == ["activated"]
            rows = [
                n
                for n in self.nodes
                if n["metadata"]["user_name"] == cube
                and n["metadata"]["memory_type"] in types
                and n["metadata"]["status"] in body["status"]
                and match(n, body.get("filter"))
            ]
            rows.sort(key=lambda n: (n["metadata"]["created_at"], n["id"]), reverse=True)
            total = len(rows)
            start = (body["page"] - 1) * body["page_size"]
            data = {
                tab: [
                    {
                        "cube_id": cube,
                        "memories": rows[start : start + body["page_size"]],
                        "total_nodes": total,
                    }
                ]
            }
        return httpx.Response(200, json={"code": 200, "data": data, "message": "success"})


def client_for(tmp_path, upstream=None, default="a"):
    upstream = upstream or Upstream()
    settings = Settings(
        auth_enabled=False,
        upstream_url="http://memos.test/",
        projects_seed=(("a", "cube-a"), ("b", "cube-b")),
        default_project_id=default,
        data_dir=tmp_path,
    )
    return TestClient(create_app(settings, transport=httpx.MockTransport(upstream)))


def query(tab="text_mem", **extra):
    return {"projectId": "a", "memoryDetailType": tab, "pageNum": 1, "pageSize": 10, **extra}


@pytest.mark.parametrize("tab", [*TAB_TYPES, "profile_mem"])
def test_six_lists_and_details_match_contract_and_drop_large_fields(tmp_path, tab):
    upstream = Upstream()
    with client_for(tmp_path, upstream) as client:
        result = client.post("/api/dashboard/project/memory/list", json=query(tab))
        assert result.status_code == 200, result.text
        body = result.json()
        data = body["data"]
        assert body["code"] == 0 and data["current"] == 1
        assert data["total"] > 0
        row = data["memListItemResponseList"][0]
        # 全 tab 通用格式钉（ISO「T」形态必不过 fullmatch；text_mem 首行 day=25 与其余 tab 不同）
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", row["createTime"]), tab
        detail = client.post(
            "/api/dashboard/project/memory/detail",
            json={"memoryId": row["memoryId"], "memType": tab},
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["memoryId"] == row["memoryId"]
        assert "metadata" not in row
        assert (
            "embedding" not in result.text
            and "sources" not in result.text
            and "history" not in result.text
        )
        assert len(result.content) < 200 * 1024
        if tab == "profile_mem":
            assert row["profileTemplateId"] == "template-real"
            assert row["profileInstanceId"] == "profile_" + "a" * 32


def test_filter_then_count_and_stable_pagination(tmp_path):
    with client_for(tmp_path) as client:
        pages = [
            client.post("/api/dashboard/project/memory/list", json=query(pageNum=n)).json()["data"]
            for n in [1, 2, 3, 4]
        ]
        assert [p["total"] for p in pages] == [25] * 4
        assert [len(p["memListItemResponseList"]) for p in pages] == [10, 10, 5, 0]
        ids = [r["memoryId"] for p in pages for r in p["memListItemResponseList"]]
        assert len(set(ids)) == 25
        response = client.post(
            "/api/dashboard/project/memory/list",
            json=query(
                startTime="2026-01-10",
                endTime="2026-01-12",
                memSearchContent="Marker",
                externalUserId="u1",
                subjectType="user",
            ),
        )
        data = response.json()["data"]
        assert data["total"] == 3
        assert [r["memoryId"] for r in data["memListItemResponseList"]] == [
            "text-11",
            "text-10",
            "text-09",
        ]


@pytest.mark.parametrize("kb,expected", [("kb-a", 1), ("kb-array", 1), ("missing", 0)])
def test_knowledgebase_filter_is_applied(tmp_path, kb, expected):
    with client_for(tmp_path) as client:
        data = client.post(
            "/api/dashboard/project/memory/list", json=query(knowledgeBaseId=kb)
        ).json()["data"]
    assert data["total"] == expected


def test_project_range_and_type_boundaries(tmp_path):
    upstream = Upstream()
    with client_for(tmp_path, upstream) as client:
        assert (
            client.post(
                "/api/dashboard/project/memory/list", json=query(projectId="not-registered")
            ).status_code
            == 404
        )
        assert not upstream.calls
        b = client.post("/api/dashboard/project/memory/list", json=query(projectId="b")).json()[
            "data"
        ]
        assert [r["memoryId"] for r in b["memListItemResponseList"]] == ["foreign"]
        assert (
            client.post(
                "/api/dashboard/project/memory/detail",
                json={"memoryId": "foreign", "memType": "text_mem"},
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/dashboard/project/memory/detail",
                json={"memoryId": "EventMemory", "memType": "text_mem"},
            ).status_code
            == 400
        )
    with client_for(tmp_path, upstream, default="b") as client:
        assert (
            client.post(
                "/api/dashboard/project/memory/detail",
                json={"memoryId": "text-00", "memType": "text_mem"},
            ).status_code
            == 404
        )


@pytest.mark.parametrize(
    "change",
    [
        {"pageNum": 0},
        {"pageSize": 0},
        {"pageSize": 101},
        {"pageNum": True},
        {"pageNum": 1.5},
        {"memoryDetailType": "bad"},
        {"subjectType": "group"},
        {"startTime": "invalid"},
        {"startTime": "2026-01-01T00:00:00"},
        {"startTime": "2026-01-03", "endTime": "2026-01-01"},
    ],
)
def test_bad_queries_fail_before_upstream(tmp_path, change):
    upstream = Upstream()
    with client_for(tmp_path, upstream) as client:
        response = client.post("/api/dashboard/project/memory/list", json=query(**change))
    assert response.status_code == 400 and response.json()["code"] == 40000
    assert not upstream.calls


@pytest.mark.parametrize(
    "failure,status",
    [
        ("http", 502),
        ("json", 502),
        ("business", 502),
        ("shape", 502),
        ("connect", 503),
        ("timeout", 504),
    ],
)
def test_upstream_failures_are_not_success_empty_lists(tmp_path, failure, status):
    def upstream(request):
        if failure == "connect":
            raise httpx.ConnectError("private-key", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private-key", request=request)
        if failure == "http":
            return httpx.Response(500, text="private-key")
        if failure == "json":
            return httpx.Response(200, text="not-json")
        return httpx.Response(200, json={"code": 400 if failure == "business" else 200, "data": {}})

    with client_for(tmp_path, upstream) as client:
        response = client.post("/api/dashboard/project/memory/list", json=query())
    assert response.status_code == status
    assert response.json()["code"] != 0 and response.json()["data"] is None
    assert "private-key" not in response.text


def test_detail_is_not_list_projection_and_omits_unknown_ownership(tmp_path):
    upstream = Upstream()
    raw = upstream.nodes[0]
    raw["memory"] = "中文" * 600
    raw["metadata"] = {
        "status": "activated",
        "memory_type": "UserMemory",
        "created_at": "2026-01-01T00:00:00Z",
        "user_name": "cube-a",
    }
    with client_for(tmp_path, upstream) as client:
        row = client.post(
            "/api/dashboard/project/memory/list", json=query(memSearchContent="中文")
        ).json()["data"]["memListItemResponseList"][0]
        detail = client.post(
            "/api/dashboard/project/memory/detail",
            json={"memoryId": "text-00", "memType": "text_mem"},
        ).json()["data"]
    assert len(row["memoryContent"]) == 501
    assert detail["memoryContent"] == "中文" * 600
    assert "subjectType" not in detail and "externalUserId" not in detail


def test_non_active_detail_and_invalid_id_are_not_readable(tmp_path):
    upstream = Upstream()
    upstream.nodes[0]["metadata"]["status"] = "deleted"
    with client_for(tmp_path, upstream) as client:
        assert (
            client.post(
                "/api/dashboard/project/memory/detail",
                json={"memoryId": "text-00", "memType": "text_mem"},
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/dashboard/project/memory/detail",
                json={"memoryId": "../escape", "memType": "text_mem"},
            ).status_code
            == 400
        )


def test_no_implicit_project_configuration_exposes_memories():
    app = create_app(
        Settings(auth_enabled=False, upstream_url="http://unused.test/"),
        transport=httpx.MockTransport(lambda r: pytest.fail("must not access upstream")),
    )
    with TestClient(app) as client:
        assert client.post("/api/dashboard/project/memory/list", json=query()).status_code == 404
        assert (
            client.post(
                "/api/dashboard/project/memory/detail",
                json={"memoryId": "text-00", "memType": "text_mem"},
            ).status_code
            == 503
        )


def test_project_dictionary_is_only_configured_real_mapping(tmp_path):
    with client_for(tmp_path) as client:
        response = client.get("/api/dashboard/project/dict")
    assert response.status_code == 200
    assert [(r["id"], r["status"]) for r in response.json()["data"]] == [
        ("a", "ENABLED"),
        ("b", "DISABLED"),
    ]


@pytest.mark.parametrize(
    "data",
    [
        {"status": {}},
        {"status": []},
        {"success": False},
        {"text_mem": []},
        {"text_mem": [{"cube_id": "wrong", "memories": [], "total_nodes": 0}]},
        {"text_mem": [{"cube_id": "cube-a", "memories": [], "total_nodes": True}]},
    ],
)
def test_malformed_product_payload_is_bad_gateway_not_internal_error(tmp_path, data):
    with client_for(
        tmp_path, lambda request: httpx.Response(200, json={"code": 200, "data": data})
    ) as client:
        response = client.post("/api/dashboard/project/memory/list", json=query())
    assert response.status_code == 502
    assert response.json()["code"] == 50200


@pytest.mark.parametrize("bad_metadata", [None, [], "invalid"])
def test_malformed_detail_metadata_is_bad_gateway(tmp_path, bad_metadata):
    def upstream(request):
        return httpx.Response(
            200,
            json={"code": 200, "data": {"id": "text-00", "memory": "x", "metadata": bad_metadata}},
        )

    with client_for(tmp_path, upstream) as client:
        response = client.post(
            "/api/dashboard/project/memory/detail",
            json={"memoryId": "text-00", "memType": "text_mem"},
        )
    assert response.status_code == 502


def test_list_does_not_accept_nonactive_rows_or_foreign_tenant(tmp_path):
    for change in [{"status": "archived"}, {"user_name": "cube-b"}]:
        raw = node("bad")
        raw["metadata"].update(change)

        def upstream(request):
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "text_mem": [{"cube_id": "cube-a", "memories": [raw], "total_nodes": 1}]
                    },
                },
            )

        with client_for(tmp_path, upstream) as client:
            assert (
                client.post("/api/dashboard/project/memory/list", json=query()).status_code == 502
            )


def test_memory_response_size_guard(tmp_path, monkeypatch):
    monkeypatch.setattr("server.backend.memory_upstream.MAX_RESPONSE_BYTES", 64, raising=False)
    with client_for(tmp_path) as client:
        response = client.post("/api/dashboard/project/memory/list", json=query())
    assert response.status_code == 502


def test_encoded_memory_body_is_never_consumed(tmp_path):
    class Body(httpx.AsyncByteStream):
        read = False

        async def __aiter__(self):
            self.read = True
            yield b"must not decode"

    body = Body()
    with client_for(
        tmp_path, lambda r: httpx.Response(200, headers={"content-encoding": "gzip"}, stream=body)
    ) as client:
        assert client.post("/api/dashboard/project/memory/list", json=query()).status_code == 502
    assert not body.read


@pytest.mark.parametrize(
    "filters",
    [
        {"endTime": "9999-12-31"},
        {"endTime": "9999-12-31T23:59:59-08:00"},
        {"startTime": "0001-01-01T00:00:00+08:00"},
    ],
)
def test_out_of_range_utc_boundaries_are_parameter_errors(tmp_path, filters):
    with client_for(tmp_path) as client:
        response = client.post("/api/dashboard/project/memory/list", json=query(**filters))
    assert response.status_code == 400
    assert response.json()["code"] == 40000
