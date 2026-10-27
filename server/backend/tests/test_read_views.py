import json

import httpx
import pytest
from fastapi.testclient import TestClient
from server.backend.app import create_app
from server.backend.tests.test_accounts import configured, login, seed
from server.backend.tests.test_audit_api import validate_contract

BASE = "/api/dashboard/"


class ReadTransport:
    def __init__(self):
        self.calls = []
        self.cube = "cube-a"
        self.deleted = False
        self.bad = False

    def __call__(self, request):
        body = json.loads(request.content or b"{}")
        self.calls.append((request.url, body))
        if request.url.path.endswith("list_profile_memories"):
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "cube_id": self.cube,
                        "memories": [],
                        "total": 0,
                    },
                },
            )
        if request.url.path.endswith("get_memory_by_ids"):
            assert request.url.params["mem_cube_id"] == "cube-a"
            assert body == {"memory_ids": ["one"], "include_sources": False}
            data = {
                "memories": [
                    {
                        "id": "one",
                        "memory": "x" * 250,
                        "metadata": {
                            "user_name": self.cube,
                            "memory_type": "InsightMemory",
                            "status": "deleted" if self.deleted else "activated",
                            "api_key": "SECRET",
                        },
                    }
                ]
            }
        elif request.url.path.endswith("get_memory_graph"):
            assert body["mem_cube_id"] == "cube-a"
            assert body["status"] == ["activated"]
            nodes = [
                {
                    "id": name,
                    "memory": "x" * 121,
                    "memoryTruncated": True,
                    "memoryType": "LongTermMemory",
                    "status": "activated",
                    "createdAt": "2026-10-26T18:30:02.075819+00:00",
                    "sessionId": "session",
                    "userId": "user",
                    "sourceMemoryIds": [],
                    "secret": "SECRET",
                }
                for name in ("one", "two")
            ]
            nodes.append(dict(nodes[0], id="deleted", status="deleted"))
            data = {
                "cube_id": self.cube,
                "nodes": nodes,
                "edges": [
                    {"source": "one", "target": "two", "type": "PARENT"},
                    {"source": "one", "target": "absent", "type": "PARENT"},
                ],
                "total_nodes": 4,
                "truncated": True,
            }
            if self.bad:
                data["nodes"][0]["sourceMemoryIds"] = [{}]
        else:
            assert request.method == "GET" and request.url.path.endswith("/models")
            data = {
                "records": [
                    {
                        "source": "MEMRADER_MODEL",
                        "model": "claude-haiku-4-5-20251001",
                        "spec": "https://user:SECRET@api.example.com:8443/v1?key=SECRET#SECRET",
                        "role": "SECRET",
                        "group": "chat",
                        "api_key": "SECRET",
                    },
                    {"source": "MOS_EMBEDDER_MODEL", "model": "BAAI/bge-m3", "spec": "1024 维"},
                    {"source": "UNKNOWN_SECRET", "model": "SECRET", "spec": "SECRET"},
                ],
                "total": 3,
            }
        return httpx.Response(200, json={"code": 200, "data": data})


def make(tmp_path, remote):
    return create_app(configured(tmp_path), transport=httpx.MockTransport(remote))


def test_graph_projection_truncation_fulltext_and_models_redaction(tmp_path):
    remote = ReadTransport()
    with TestClient(make(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        response = web.get(BASE + "graph/memories", params={"tenant": "a", "limit": 2})
        assert response.status_code == 200, response.text
        validate_contract(response.json(), "GraphMemoryGraph")
        graph = response.json()["data"]
        assert graph["tenant"] == "a"
        assert len(graph["nodes"]) == 2
        assert all(n["createdAt"] == "2026-10-27 02:30:02" for n in graph["nodes"])
        assert all(len(n["label"]) <= 120 and n["labelTruncated"] for n in graph["nodes"])
        assert graph["stats"]["truncated"] is True
        assert graph["stats"]["clusters"] == 1 and graph["stats"]["orphans"] == 0
        assert {e["kind"] for e in graph["links"]} == {"parent", "session"}
        assert "SECRET" not in response.text
        full = web.get(BASE + "graph/memory", params={"tenant": "a", "id": "one"})
        validate_contract(full.json(), "GraphMemory")
        assert full.json()["data"] == {"id": "one", "memory": "x" * 250}
        models = web.get(BASE + "models")
        validate_contract(models.json(), "ModelRecords")
        assert models.json()["data"]["total"] == 2
        assert models.json()["data"]["records"][0]["spec"] == "api.example.com:8443"
        assert "SECRET" not in models.text
        assert models.headers["cache-control"] == "no-store"
        remote.deleted = True
        assert (
            web.get(BASE + "graph/memory", params={"tenant": "a", "id": "one"}).status_code == 404
        )


@pytest.mark.parametrize("timestamp", ["", "not-a-time"])
def test_graph_time_empty_or_invalid(timestamp):
    from server.backend.errors import ApiError
    from server.backend.read_views import project_graph

    data = {
        "cube_id": "a",
        "edges": [],
        "total_nodes": 1,
        "truncated": False,
        "nodes": [
            {
                "id": "one",
                "memory": "sample",
                "memoryTruncated": False,
                "memoryType": "LongTermMemory",
                "status": "activated",
                "createdAt": timestamp,
            }
        ],
    }
    if timestamp:
        with pytest.raises(ApiError):
            project_graph(data, "a", "a", [], 10)
    else:
        assert project_graph(data, "a", "a", [], 10)["nodes"][0]["createdAt"] == ""


def test_graph_validation_permissions_and_untrusted_payloads(tmp_path):
    remote = ReadTransport()
    with TestClient(make(tmp_path, remote)) as web:
        seed(web.app)
        assert web.get(BASE + "models").status_code == 401
        login(web)
        for params in ({"limit": 0}, {"limit": 2001}, {"types": "Invalid"}):
            assert web.get(BASE + "graph/memories", params=params).status_code == 400
        before = len(remote.calls)
        assert web.get(BASE + "graph/memories", params={"tenant": "cube-a"}).status_code == 404
        assert len(remote.calls) == before
        remote.cube = "cube-b"
        assert web.get(BASE + "graph/memories").status_code == 502
        assert web.get(BASE + "graph/memory", params={"id": "one"}).status_code == 404
        remote.cube = "cube-a"
        remote.bad = True
        assert web.get(BASE + "graph/memories").status_code == 502
        user = web.app.state.identity.create_account("reader", "reader-password-123")
        web.post("/admin/tenants/a/members", json={"account_id": user["id"], "role": "viewer"})
        login(web, "reader", "reader-password-123")
        assert web.get(BASE + "graph/memories", params={"tenant": "b"}).status_code == 404
        assert web.get(BASE + "models").status_code == 200
        remote.bad = False
        assert web.get(BASE + "graph/memories", params={"tenant": "a"}).status_code == 200


@pytest.mark.parametrize(
    "model",
    ["sk-abcdefghijklmnopqrstuvwxyz", "https://user:password@host/model", "model?api_key=SECRET"],
)
def test_model_identifier_cannot_smuggle_credentials(tmp_path, model):
    def remote(request):
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {"records": [{"source": "MEMRADER_MODEL", "model": model, "spec": ""}]},
            },
        )

    with TestClient(make(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        result = web.get(BASE + "models")
        assert result.status_code == 502
        assert model not in result.text


def test_graph_response_budget_provenance_and_derived_edges():
    from server.backend.read_views import project_graph

    nodes = [
        {
            "id": str(i) + "x" * 200,
            "memory": "m" * 121,
            "memoryTruncated": True,
            "memoryType": "InsightMemory",
            "status": "activated",
            "sourceMemoryIds": [],
        }
        for i in range(2000)
    ]
    nodes[0]["sourceMemoryIds"] = [nodes[1]["id"], "not-on-this-page"]
    edges = [
        {
            "source": nodes[i % 2000]["id"],
            "target": nodes[(i + 1 + i // 2000) % 2000]["id"],
            "type": "PARENT",
        }
        for i in range(20000)
    ]
    graph = project_graph(
        {"cube_id": "a", "nodes": nodes, "edges": edges, "total_nodes": 2000, "truncated": False},
        "a",
        "a",
        [],
        2000,
    )
    assert graph["stats"]["truncated"]
    assert len(json.dumps(graph, ensure_ascii=False).encode()) < 2 * 1024 * 1024
    assert "not-on-this-page" in graph["nodes"][0]["sourceIds"]
    assert all(e["target"] != "not-on-this-page" for e in graph["links"])


def test_graph_types_current_selection_and_readonly_methods(tmp_path):
    remote = ReadTransport()
    with TestClient(make(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        graph = web.get(BASE + "graph/memories", params={"types": "PreferenceMemory"})
        assert graph.status_code == 200
        assert not graph.json()["data"]["nodes"]
        assert remote.calls[-1][1]["memory_types"] == ["PreferenceMemory"]
        assert remote.calls[-1][1]["mem_cube_id"] == "cube-a"
        assert web.post(BASE + "models", json={}).status_code == 405
        assert web.post(BASE + "graph/memories", json={}).status_code == 405
