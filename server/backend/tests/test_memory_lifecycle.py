import json

import httpx
import pytest
from fastapi.testclient import TestClient
from server.backend.app import create_app
from server.backend.settings import Settings


class Upstream:
    def __init__(self):
        self.rows = {
            "one": {"status": "activated", "memory_type": "UserMemory"},
            "two": {"status": "activated", "memory_type": "UserMemory"},
        }
        self.operations = {}
        self.transitions = []
        self.fail = set()
        self.probes = []

    def __call__(self, request):
        body = json.loads(request.content)
        path = request.url.path
        if path.endswith("inspect_memory_lifecycle"):
            self.probes.append(body)
            row = self.rows.get(body["memory_id"]) if body["mem_cube_id"] == "cube-a" else None
            return httpx.Response(
                200,
                json={
                    "code": 200 if row else 404,
                    "data": {"memory_id": body["memory_id"], **row} if row else None,
                },
            )
        if path.endswith("get_memory_operation"):
            data = self.operations.get(body["operation_id"])
        else:
            self.transitions.append(body)
            identifier = body["memory_id"]
            if identifier in self.fail:
                raise httpx.ReadTimeout("test outage")
            target = "deleted" if body["target_status"] == "deleted" else "activated"
            self.rows[identifier].update(status=target, previous_status="activated")
            data = {
                "memory_id": identifier,
                "operation_id": body["operation_id"],
                "state": "confirmed",
                "target_status": target,
                "phase": "confirmed",
                "success": True,
                "retryable": False,
                "kind": body["kind"],
                "vector_state": "not_applicable" if body["kind"] == "profile" else "confirmed",
            }
            self.operations[body["operation_id"]] = data
        return httpx.Response(200, json={"code": 200, "data": data})


def client(tmp_path, upstream):
    return TestClient(
        create_app(
            Settings(
                auth_enabled=False,
                projects_seed=(("a", "cube-a"), ("b", "cube-b")),
                default_project_id="a",
                data_dir=tmp_path,
            ),
            transport=httpx.MockTransport(upstream),
        )
    )


def body(**extra):
    return {
        "projectId": "a",
        "memType": "text_mem",
        "memory_ids": ["one", "two"],
        "operationId": "batch-1",
        **extra,
    }


def test_batch_delete_restore_and_idempotent_receipts_survive_backend_restart(tmp_path):
    upstream = Upstream()
    with client(tmp_path, upstream) as web:
        response = web.post("/api/dashboard/project/memory/delete", json=body())
        assert response.status_code == 200 and response.json()["code"] == 0
        assert response.json()["data"]["deleted"] == 2
        assert (
            web.post("/api/dashboard/project/memory/delete", json=body()).json() == response.json()
        )
        assert len(upstream.transitions) == 2
    with client(tmp_path, upstream) as web:
        query = web.get(
            "/api/dashboard/project/memory/operation",
            params={"projectId": "a", "operationId": "batch-1"},
        )
        assert query.json()["data"]["state"] == "confirmed"
        restored = web.post(
            "/api/dashboard/project/memory/restore", json=body(operationId="restore")
        )
        assert restored.json()["data"]["restored"] == 2
        conflict = web.post("/api/dashboard/project/memory/delete", json=body(memory_ids=["one"]))
        assert conflict.status_code == 409


def test_partial_timeout_is_not_success_and_retries_only_unconfirmed_ids(tmp_path):
    upstream = Upstream()
    upstream.fail.add("two")
    with client(tmp_path, upstream) as web:
        response = web.post("/api/dashboard/project/memory/delete", json=body())
        assert response.status_code == 503 and response.json()["code"] != 0
        data = response.json()["data"]
        assert data["deleted"] == 1 and data["operationId"] == "batch-1"
    upstream.fail.clear()
    with client(tmp_path, upstream) as web:
        retry = web.post(
            "/api/dashboard/project/memory/retry", json={"projectId": "a", "operationId": "batch-1"}
        )
        assert retry.json()["code"] == 0 and retry.json()["data"]["deleted"] == 2
        assert len([t for t in upstream.transitions if t["memory_id"] == "one"]) == 1


@pytest.mark.parametrize("state,vector", [("pending", "unconfirmed"), ("confirmed", "unconfirmed")])
def test_pending_or_unverified_vector_never_becomes_success(tmp_path, state, vector):
    upstream = Upstream()

    def handler(request):
        if request.url.path.endswith("transition_memory"):
            data = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "memory_id": data["memory_id"],
                        "operation_id": data["operation_id"],
                        "kind": "graph",
                        "target_status": "deleted",
                        "state": state,
                        "success": state == "confirmed",
                        "vector_state": vector,
                    },
                },
            )
        return upstream(request)

    with client(tmp_path, handler) as web:
        response = web.post("/api/dashboard/project/memory/delete", json=body(memory_ids=["one"]))
    assert response.status_code == 503 and response.json()["code"] != 0
    assert response.json()["data"]["deleted"] == 0


@pytest.mark.parametrize(
    "change", [{"memory_ids": ["one", "missing"]}, {"projectId": "b"}, {"memType": "event_mem"}]
)
def test_preflight_all_targets_before_any_write(tmp_path, change):
    upstream = Upstream()
    with client(tmp_path, upstream) as web:
        response = web.post("/api/dashboard/project/memory/delete", json=body(**change))
    assert response.status_code in (400, 404, 409)
    assert upstream.transitions == []
    assert upstream.probes


def test_rejects_cross_origin_and_invalid_requests(tmp_path):
    upstream = Upstream()
    with client(tmp_path, upstream) as web:
        assert (
            web.post(
                "/api/dashboard/project/memory/delete",
                json=body(),
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        for bad in ({"memory_ids": []}, {"memory_ids": ["one", "one"]}, {"memType": "unknown"}):
            assert (
                web.post("/api/dashboard/project/memory/delete", json=body(**bad)).status_code
                == 400
            )
        assert (
            web.get(
                "/api/dashboard/project/memory/operation",
                params={"projectId": "b", "operationId": "batch-1"},
            ).status_code
            == 404
        )
        assert (
            web.get(
                "/api/dashboard/project/memory/operation",
                params={"projectId": "a", "operationId": "../bad"},
            ).status_code
            == 400
        )


def test_profile_delete_uses_field_transition_and_reports_no_vector(tmp_path):
    upstream = Upstream()
    upstream.rows["one"]["memory_type"] = "ProfileMemory"
    with client(tmp_path, upstream) as web:
        assert (
            web.post(
                "/api/dashboard/project/memory/delete",
                json=body(memType="profile_mem", memory_ids=["one"]),
            ).json()["code"]
            == 0
        )
    assert upstream.transitions[0]["kind"] == "profile"
