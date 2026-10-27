import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from server.backend.tests.test_accounts import login, seed
from server.backend.tests.test_audit_api import validate_contract
from server.backend.tests.test_read_views import BASE, ReadTransport, make


def field(number=0, path=None):
    path = path or "basic.field_" + str(number)
    instance = "profile_" + "a" * 32
    return {
        "id": instance + "_" + path,
        "memory": path + ": " + "value" * 40,
        "memory_type": "ProfileMemory",
        "status": "activated",
        "user_name": "cube-a",
        "subject_type": "user",
        "subject_id": "profile-user",
        "profile_instance_id": instance,
        "profile_path": path,
        "created_at": "2026-10-26T00:00:00Z",
        "secret": "HIDDEN",
    }


class ProfileTransport(ReadTransport):
    def __init__(self, count=2):
        super().__init__()
        self.rows = [field(i) for i in range(count)]
        self.bad_page = False

    def __call__(self, request):
        body = json.loads(request.content or b"{}")
        if request.url.path.endswith("list_profile_memories"):
            self.calls.append((request.url, body))
            size = body["page_size"]
            assert 1 <= size <= 100
            start = (body["page"] - 1) * size
            rows = copy.deepcopy(self.rows[start : start + size])
            if self.bad_page and rows:
                rows[0]["user_name"] = "foreign"
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {"cube_id": "cube-a", "memories": rows, "total": len(self.rows)},
                },
            )
        if request.url.path.endswith("get_profile_memory"):
            self.calls.append((request.url, body))
            row = next((row for row in self.rows if row["id"] == body["memory_id"]), None)
            if body["mem_cube_id"] != "cube-a":
                row = None
            return httpx.Response(200, json={"code": 200, "data": row})
        return super().__call__(request)


def test_graph_combines_profile_fields_same_ids_fulltext_and_deletion(tmp_path):
    remote = ProfileTransport()
    with TestClient(make(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        response = web.get(BASE + "graph/memories", params={"tenant": "a"})
        assert response.status_code == 200
        validate_contract(response.json(), "GraphMemoryGraph")
        graph = response.json()["data"]
        assert graph["stats"]["nodes"] == 4  # two regular nodes plus two profile fields
        profiles = [n for n in graph["nodes"] if n["memoryType"] == "ProfileMemory"]
        assert {n["id"] for n in profiles} == {r["id"] for r in remote.rows}
        assert all(n["createdAt"] == "2026-10-26 08:00:00" for n in profiles)
        assert all(n["labelTruncated"] and len(n["label"]) <= 120 for n in profiles)
        assert all(n["sessionId"] == "" and n["sourceIds"] == [] for n in profiles)
        assert all(n["clusterId"] == n["id"] for n in profiles)
        assert all(n["userId"] == "profile-user" for n in profiles)
        assert "HIDDEN" not in response.text
        identifier = remote.rows[0]["id"]
        full = web.get(BASE + "graph/memory", params={"tenant": "a", "id": identifier})
        assert full.status_code == 200
        validate_contract(full.json(), "GraphMemory")
        assert full.json()["data"] == {"id": identifier, "memory": remote.rows[0]["memory"]}
        assert (
            web.get(BASE + "graph/memory", params={"tenant": "b", "id": identifier}).status_code
            == 404
        )
        removed = remote.rows.pop(0)
        graph = web.get(BASE + "graph/memories", params={"tenant": "a"}).json()["data"]
        assert identifier not in {n["id"] for n in graph["nodes"]}
        assert (
            web.get(BASE + "graph/memory", params={"tenant": "a", "id": identifier}).status_code
            == 404
        )
        remote.rows.append(removed)
        assert (
            web.get(BASE + "graph/memory", params={"tenant": "a", "id": identifier}).status_code
            == 200
        )


def test_profile_only_pagination_limits_and_no_graph_request(tmp_path):
    remote = ProfileTransport(205)
    with TestClient(make(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        data = web.get(
            BASE + "graph/memories", params={"tenant": "a", "types": "ProfileMemory"}
        ).json()["data"]
        assert data["stats"]["nodes"] == 205 and not data["stats"]["truncated"]
        assert len(remote.calls) == 3
        assert all(url.path.endswith("list_profile_memories") for url, _ in remote.calls)
        data = web.get(
            BASE + "graph/memories", params={"tenant": "a", "types": "ProfileMemory", "limit": 1}
        ).json()["data"]
        assert data["stats"]["nodes"] == 1 and data["stats"]["truncated"]
        remote.calls.clear()
        web.get(BASE + "graph/memories", params={"tenant": "a", "types": "PreferenceMemory"})
        assert len(remote.calls) == 1 and remote.calls[0][0].path.endswith("get_memory_graph")


@pytest.mark.parametrize("bad", ["foreign", "duplicate", "inactive", "invalid_time"])
def test_profile_graph_failures_never_become_partial_success(tmp_path, bad):
    remote = ProfileTransport()
    if bad == "foreign":
        remote.bad_page = True
    elif bad == "duplicate":
        remote.rows[1] = copy.deepcopy(remote.rows[0])
    elif bad == "inactive":
        remote.rows[0]["status"] = "deleted"
    elif bad == "invalid_time":
        remote.rows[0]["created_at"] = "invalid"
    with TestClient(make(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        assert web.get(BASE + "graph/memories", params={"tenant": "a"}).status_code == 502


def test_long_profile_id_is_preserved_and_fulltext_resolves(tmp_path):
    remote = ProfileTransport(0)
    remote.rows = [field(path="group." + "x" * 270)]
    with TestClient(make(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        result = web.get(BASE + "graph/memories", params={"tenant": "a"}).json()["data"]
        identifier = remote.rows[0]["id"]
        assert identifier in {n["id"] for n in result["nodes"]}
        assert (
            web.get(BASE + "graph/memory", params={"tenant": "a", "id": identifier}).status_code
            == 200
        )


def test_profile_fulltext_byte_budget_and_project_authorization(tmp_path):
    remote = ProfileTransport(1)
    with TestClient(make(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        remote.rows[0]["memory"] = "\n" * (600 * 1024)
        identifier = remote.rows[0]["id"]
        assert (
            web.get(BASE + "graph/memory", params={"tenant": "a", "id": identifier}).status_code
            == 413
        )
        remote.calls.clear()
        assert web.get(BASE + "graph/memories", params={"tenant": "unknown"}).status_code == 404
        assert remote.calls == []
