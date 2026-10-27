"""Exercise the metadata contract with isolated upstream HTTP responses."""

import asyncio
import gzip
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from server.backend.app import create_app
from server.backend.settings import Settings

HEALTH = {
    "status": "healthy",
    "service": "memos",
    "version": "1.0.1",
    "contract_version": 2,
}
TOOLS = {
    "add_message",
    "search_memory",
    "get_user_profile",
    "delete_memory",
    "add_feedback",
}


def client_for(handler):
    return TestClient(
        create_app(
            Settings(
                auth_enabled=False, upstream_url="http://memos.test/", upstream_timeout_seconds=1
            ),
            transport=httpx.MockTransport(handler),
        )
    )


def validate_meta_contract(body):
    spec = json.loads(
        (Path(__file__).resolve().parents[3] / "openapi.json").read_text(encoding="utf-8")
    )
    schema = spec["components"]["responses"]["Meta"]["content"]["application/json"]["schema"]
    Draft202012Validator({**schema, "components": spec["components"]}).validate(body)


def test_meta_uses_real_health_and_matches_dashboard_contract():
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json=HEALTH)

    with client_for(upstream) as client:
        response = client.get("/api/meta")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"code", "data", "message"}
    assert body["code"] == 0
    assert body["data"]["mode"] == "live"
    assert body["data"]["service"] == "memos-dashboard-backend"
    assert body["data"]["health"] == HEALTH
    assert body["data"]["keyPrefix"] == "sk-"
    assert body["data"]["authEnabled"] is False
    assert {tool["name"] for tool in body["data"]["tools"]} == TOOLS
    assert all(tool["title"] for tool in body["data"]["tools"])
    assert len(calls) == 1
    assert str(calls[0].url) == "http://memos.test/health"
    assert "authorization" not in calls[0].headers
    assert response.headers["cache-control"] == "no-store"
    validate_meta_contract(body)


def test_health_projects_only_safe_fields():
    health = {**HEALTH, "api_key": "private-test-value", "configuration": {"password": "hidden"}}
    with client_for(lambda request: httpx.Response(200, json=health)) as client:
        response = client.get("/api/meta")
    assert response.json()["data"]["health"] == HEALTH
    assert "private-test-value" not in response.text
    assert "password" not in response.text


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadError])
def test_upstream_connection_failure_is_uniform_and_does_not_leak_details(failure):
    def unavailable(request):
        raise failure("sensitive upstream connection details", request=request)

    with client_for(unavailable) as client:
        response = client.get("/api/meta")
    assert response.status_code == 503
    assert response.json()["code"] == 50300
    assert response.json()["data"] is None
    assert response.json()["message"]
    assert "sensitive" not in response.text


@pytest.mark.parametrize("failure", [httpx.ConnectTimeout, httpx.ReadTimeout])
def test_upstream_timeout_is_uniform(failure):
    def timeout(request):
        raise failure("sensitive timeout details", request=request)

    with client_for(timeout) as client:
        response = client.get("/api/meta")
    assert response.status_code == 504
    assert response.json()["code"] == 50400
    assert response.json()["data"] is None
    assert "sensitive" not in response.text


@pytest.mark.parametrize("status", [301, 401, 404, 429, 500, 503])
def test_upstream_non_success_status_is_not_live_success(status):
    count = []

    def upstream(request):
        count.append(1)
        return httpx.Response(
            status, text="sensitive upstream body", headers={"location": "/login"}
        )

    with client_for(upstream) as client:
        response = client.get("/api/meta")
    assert response.status_code == 502
    assert response.json()["code"] == 50200
    assert response.json()["data"] is None
    assert len(count) == 1
    assert "sensitive" not in response.text


@pytest.mark.parametrize("body", ["<html>not json</html>", "null", "[]", "42"])
def test_invalid_upstream_json_or_shape_is_not_success(body):
    with client_for(lambda request: httpx.Response(200, text=body)) as client:
        response = client.get("/api/meta")
    assert response.status_code == 502
    assert response.json()["code"] == 50200
    assert response.json()["data"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"service": "mock"},
        {"contract_version": 1},
        {"contract_version": "2"},
        {"version": ""},
    ],
)
def test_upstream_identity_or_contract_mismatch_is_not_success(changes):
    with client_for(lambda request: httpx.Response(200, json={**HEALTH, **changes})) as client:
        response = client.get("/api/meta")
    assert response.status_code == 502
    assert response.json()["code"] == 50200


def test_missing_health_fields_are_rejected():
    with client_for(lambda request: httpx.Response(200, json={"status": "healthy"})) as client:
        response = client.get("/api/meta")
    assert response.status_code == 502
    assert response.json()["code"] != 0


def test_unhealthy_upstream_does_not_report_success():
    with client_for(
        lambda request: httpx.Response(200, json={**HEALTH, "status": "degraded"})
    ) as client:
        response = client.get("/api/meta")
    assert response.status_code == 503
    assert response.json()["code"] == 50300


def test_checks_are_fresh_and_can_recover_without_restarting_backend():
    calls = []

    def upstream(request):
        calls.append(1)
        if len(calls) == 2:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json=HEALTH)

    with client_for(upstream) as client:
        responses = [client.get("/api/meta") for _ in range(3)]
    assert [response.status_code for response in responses] == [200, 503, 200]
    assert len(calls) == 3


def test_local_liveness_does_not_call_upstream():
    def forbidden(request):
        raise AssertionError("Local health must not need MemOS")

    with client_for(forbidden) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "data": {"status": "healthy", "service": "memos-dashboard-backend"},
        "message": "success",
    }


def test_http_client_is_closed_with_application_lifespan():
    class Transport(httpx.MockTransport):
        closed = False

        async def aclose(self):
            self.closed = True

    transport = Transport(lambda request: httpx.Response(200, json=HEALTH))
    app = create_app(
        Settings(auth_enabled=False, upstream_url="http://memos.test/"), transport=transport
    )
    with TestClient(app) as client:
        assert client.get("/api/meta").status_code == 200
        assert not transport.closed
    assert transport.closed


def test_total_probe_deadline_is_enforced_even_if_transport_does_not_timeout():
    async def slow(request):
        await asyncio.sleep(0.15)
        return httpx.Response(200, json=HEALTH)

    app = create_app(
        Settings(
            auth_enabled=False, upstream_url="http://memos.test/", upstream_timeout_seconds=0.02
        ),
        transport=httpx.MockTransport(slow),
    )
    with TestClient(app) as client:
        response = client.get("/api/meta")
    assert response.status_code == 504
    assert response.json()["code"] == 50400


def test_oversized_health_body_is_rejected():
    payload = {**HEALTH, "padding": "x" * 65536}
    with client_for(lambda request: httpx.Response(200, json=payload)) as client:
        response = client.get("/api/meta")
    assert response.status_code == 502
    assert response.json()["code"] == 50200


def test_upstream_path_prefix_is_preserved():
    calls = []

    def upstream(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=HEALTH)

    app = create_app(
        Settings(auth_enabled=False, upstream_url="http://memos.test/prefix/"),
        transport=httpx.MockTransport(upstream),
    )
    with TestClient(app) as client:
        assert client.get("/api/meta").status_code == 200
    assert calls == ["http://memos.test/prefix/health"]


@pytest.mark.parametrize("encoding", ["gzip", "br", "deflate", "GZip", "gzip, identity"])
def test_encoded_health_is_rejected_before_reading_or_decoding_body(encoding):
    class Body(httpx.AsyncByteStream):
        read = False

        async def __aiter__(self):
            self.read = True
            yield gzip.compress(b"x" * 131072)

    body = Body()
    with client_for(
        lambda request: httpx.Response(200, headers={"Content-Encoding": encoding}, stream=body)
    ) as client:
        response = client.get("/api/meta")
    assert response.status_code == 502
    assert response.json()["code"] == 50200
    assert not body.read


def test_upstream_is_asked_for_identity_encoding():
    encodings = []

    def upstream(request):
        encodings.append(request.headers["accept-encoding"])
        return httpx.Response(200, json=HEALTH, headers={"Content-Encoding": "identity"})

    with client_for(upstream) as client:
        assert client.get("/api/meta").status_code == 200
    assert encodings == ["identity"]


def test_upstream_protocol_failure_is_bad_gateway():
    def invalid_protocol(request):
        raise httpx.RemoteProtocolError("private protocol details", request=request)

    with client_for(invalid_protocol) as client:
        response = client.get("/api/meta")
    assert response.status_code == 502
    assert response.json()["code"] == 50200
    assert "private protocol details" not in response.text
