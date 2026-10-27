import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from server.backend.app import create_app
from server.backend.settings import Settings
from server.backend.tests.test_accounts import login, seed
from server.backend.tests.test_audit_api import validate_contract


def test_fixed_endpoints_match_contract_without_upstream(tmp_path):
    def unavailable(request):
        raise AssertionError("Fixed responses must never access upstream or fetch URLs")

    with TestClient(
        create_app(
            Settings(data_dir=tmp_path, auth_enabled=True),
            transport=httpx.MockTransport(unavailable),
        )
    ) as web:
        quota = web.get("/api/user/quota/summary", params={"knowledgebaseId": "ignored"})
        assert quota.status_code == 200
        validate_contract(quota.json(), "QuotaSummary")
        assert quota.json()["code"] == 0
        assert quota.json()["data"] == {
            "memoryCount": 0,
            "knowledgebaseCount": 0,
            "knowledgebaseCapacity": 1000000,
            "allowedKnowledgebaseCount": 10,
        }
        for body in [{}, {"cycleId": "", "includeAllCycles": True}]:
            response = web.post("/api/user/subscription/details", json=body)
            assert response.status_code == 200
            validate_contract(response.json(), "SubscriptionDetails")
            assert response.json()["data"] == {"cycles": []}
        assert web.post("/api/dashboard/skill/skillMD", json={"oss_url": "x"}).status_code == 401
        seed(web.app)
        login(web)
        for url in ["", "https://example.invalid/private", "file:///not-readable"]:
            response = web.post("/api/dashboard/skill/skillMD", json={"oss_url": url})
            assert response.status_code == 200
            validate_contract(response.json(), "StringData")
            assert response.json() == {"code": 0, "data": "", "message": "success"}
            assert response.headers["cache-control"] == "no-store"
        bad = web.post("/api/dashboard/skill/skillMD", json={})
        assert bad.status_code == 400 and bad.json()["code"] == 40000
        bad = web.post("/api/user/subscription/details", json={"includeAllCycles": "true"})
        assert bad.status_code == 400 and set(bad.json()) == {"code", "data", "message"}


def test_every_spec_operation_has_handler_except_frozen_kb():
    spec = json.loads(
        (Path(__file__).resolve().parents[3] / "openapi.json").read_text(encoding="utf-8")
    )
    routes = {
        (method.lower(), route.path)
        for route in create_app().routes
        for method in getattr(route, "methods", [])
    }
    missing = {
        (method, path)
        for path, operations in spec["paths"].items()
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"} and (method, path) not in routes
    }
    assert missing == {("post", "/api/dashboard/knowledgebase/memory/list")}
