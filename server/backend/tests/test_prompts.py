import json

import httpx
import pytest
from fastapi.testclient import TestClient
from server.backend.app import create_app
from server.backend.tests.test_accounts import configured, login, seed
from server.backend.tests.test_audit_api import validate_contract

BASE = "/api/dashboard/prompt-config/"


class PromptTransport:
    def __init__(self):
        self.saved = {}
        self.calls = []

    def __call__(self, request):
        body = json.loads(request.content or b"{}")
        self.calls.append((request.url.path, body))
        cube = body.get("mem_cube_id")
        path = request.url.path.rsplit("/", 1)[-1]
        prompt = self.saved.get(cube, "default ${conversation}")
        if path == "get_extract_prompt":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "mem_cube_id": cube,
                        "prompt": prompt,
                        "updated_at": None,
                        "source": "saved" if cube in self.saved else "default",
                        "secret": "not-projected",
                    },
                },
            )
        if path == "save_extract_prompt":
            self.saved[cube] = body["prompt"]
            prompt = body["prompt"]
        elif path == "reset_extract_prompt":
            self.saved.pop(cube, None)
            prompt = "default ${conversation}"
        elif path == "generate_extract_prompt":
            prompt = "generated ${conversation}"
        elif path == "debug_extract":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "extraction_types": body.get("extraction_types")
                        or ["text_mem", "pref_mem", "event_mem"],
                        "results": [
                            {"type": "LongTermMemory", "content": prompt, "secret": "hidden"}
                        ],
                    },
                },
            )
        return httpx.Response(200, json={"code": 200, "data": {"prompt": prompt}})


def app(tmp_path, remote):
    return create_app(configured(tmp_path), transport=httpx.MockTransport(remote))


def test_read_save_generate_reset_debug_and_project_isolation(tmp_path):
    remote = PromptTransport()
    with TestClient(app(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        result = web.post(BASE + "detail", json={"projectId": "a"})
        assert result.status_code == 200, result.text
        validate_contract(result.json(), "PromptConfig")
        prompt = "custom RULE_A ${conversation}"
        assert web.post(BASE + "save", json={"projectId": "a", "prompt": prompt}).json()["data"][
            "ok"
        ]
        assert remote.saved == {"cube-a": prompt}
        assert (
            web.post(BASE + "detail", json={"projectId": "b"}).json()["data"]["prompt"]
            == "default ${conversation}"
        )
        generated = web.post(
            BASE + "generate", json={"projectId": "a", "description": "more precise"}
        )
        validate_contract(generated.json(), "Prompt")
        assert remote.saved["cube-a"] == prompt
        debug = web.post(
            BASE + "debug/extract",
            json={"projectId": "a", "messages": [{"role": "user", "content": "extract"}]},
        )
        validate_contract(debug.json(), "ExtractDebug")
        assert debug.json()["data"]["results"][0]["content"] == prompt
        assert "secret" not in debug.text
        assert web.post(BASE + "reset-default", json={"projectId": "a"}).status_code == 200
        assert not remote.saved


def test_prompt_validation_and_role_permissions_before_model_call(tmp_path):
    remote = PromptTransport()
    with TestClient(app(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        for body in (
            {"projectId": "a", "prompt": "missing placeholder"},
            {"projectId": "a", "prompt": ""},
            {"projectId": "a", "prompt": "x" * 20001},
        ):
            assert web.post(BASE + "save", json=body).status_code == 400
        assert (
            web.post(BASE + "debug/extract", json={"projectId": "a", "messages": []}).status_code
            == 400
        )
        assert (
            web.post(
                BASE + "debug/extract",
                json={
                    "projectId": "a",
                    "messages": [{"role": "user", "content": "x"}],
                    "extractionTypes": ["profile_mem"],
                },
            ).status_code
            == 400
        )
        user = web.app.state.identity.create_account("viewer", "viewer-password-123")
        web.post("/admin/tenants/a/members", json={"account_id": user["id"], "role": "viewer"})
        login(web, "viewer", "viewer-password-123")
        before = len(remote.calls)
        assert (
            web.post(
                BASE + "save", json={"projectId": "a", "prompt": "${conversation}"}
            ).status_code
            == 403
        )
        assert (
            web.post(BASE + "generate", json={"projectId": "a", "description": "x"}).status_code
            == 403
        )
        assert len(remote.calls) == before
        assert web.post(BASE + "detail", json={"projectId": "b"}).status_code == 404
        assert web.post(BASE + "detail", json={"projectId": "a"}).status_code == 200


@pytest.mark.parametrize(
    "failure,expected",
    [("timeout", 504), ("http", 502), ("code", 502), ("placeholder", 502), ("malformed", 502)],
)
def test_prompt_failures_are_explicit_and_never_expose_provider_secrets(
    tmp_path, failure, expected
):
    def broken(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE_PROVIDER_SECRET", request=request)
        if failure == "http":
            return httpx.Response(500, json={"detail": "PRIVATE_PROVIDER_SECRET"})
        if failure == "code":
            return httpx.Response(200, json={"code": 500, "message": "PRIVATE_PROVIDER_SECRET"})
        if failure == "placeholder":
            return httpx.Response(200, json={"code": 200, "data": {"prompt": "missing"}})
        return httpx.Response(200, json={"code": 200, "data": ["PRIVATE_PROVIDER_SECRET"]})

    with TestClient(app(tmp_path, broken)) as web:
        seed(web.app)
        login(web)
        result = web.post(BASE + "generate", json={"projectId": "a", "description": "rewrite"})
        assert result.status_code == expected
        assert result.json()["code"] != 0 and result.json()["data"] is None
        assert "PRIVATE_PROVIDER_SECRET" not in result.text


def test_prompt_editor_debug_readonly_and_disabled_project_boundaries(tmp_path):
    remote = PromptTransport()
    with TestClient(app(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        editor = web.app.state.identity.create_account("editor", "editor-password-123")
        web.post("/admin/tenants/a/members", json={"account_id": editor["id"], "role": "editor"})
        login(web, "editor", "editor-password-123")
        assert (
            web.post(
                BASE + "generate", json={"projectId": "a", "description": "rewrite"}
            ).status_code
            == 200
        )
        assert (
            web.post(
                BASE + "debug/extract",
                json={"projectId": "a", "messages": [{"role": "user", "content": "durable fact"}]},
            ).status_code
            == 200
        )
        assert not remote.saved
        assert web.post(BASE + "reset-default", json={"projectId": "a"}).status_code == 403
        login(web)
        assert (
            web.post(
                "/api/dashboard/project/update", json={"id": "a", "status": "DISABLED"}
            ).status_code
            == 200
        )
        before = len(remote.calls)
        assert web.post(BASE + "detail", json={"projectId": "a"}).status_code == 404
        assert len(remote.calls) == before


@pytest.mark.parametrize("selected", ["text_mem", "pref_mem", "event_mem"])
def test_debug_filters_native_and_canonical_names_after_normalization(tmp_path, selected):
    rows = [
        {"type": kind, "content": kind}
        for kind in (
            "LongTermMemory",
            "text_mem",
            "PreferenceMemory",
            "pref_mem",
            "EventMemory",
            "event_mem",
            "ProfileMemory",
            "profile_mem",
            "SkillMemory",
            "skill_mem",
        )
    ]
    rows.append({"type": "raw", "content": "unstructured model output"})

    def remote(request):
        return httpx.Response(
            200, json={"code": 200, "data": {"extraction_types": [selected], "results": rows}}
        )

    with TestClient(app(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        result = web.post(
            BASE + "debug/extract",
            json={
                "projectId": "a",
                "messages": [{"role": "user", "content": "synthetic input"}],
                "extractionTypes": [selected],
            },
        )
        assert result.status_code == 200
        data = result.json()["data"]
        assert data["extractionTypes"] == [selected]
        assert [row["type"] for row in data["results"]] == [selected, selected, "raw"]
