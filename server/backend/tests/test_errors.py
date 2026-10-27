"""Verify errors through the same routing stack used in production."""

import logging

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from server.backend.app import create_app
from server.backend.errors import ApiError
from server.backend.settings import Settings


def app_for_test():
    return create_app(
        Settings(auth_enabled=False, upstream_url="http://unused.test/"),
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )


def assert_error(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"code", "data", "message"}
    assert body["code"] == code
    assert body["data"] is None
    assert isinstance(body["message"], str) and body["message"]


def test_unimplemented_business_route_does_not_return_fake_success():
    with TestClient(app_for_test()) as client:
        response = client.post("/api/not-implemented", json={})
    assert_error(response, 404, 40001)


def test_method_not_allowed_uses_envelope_and_preserves_allow_header():
    with TestClient(app_for_test()) as client:
        response = client.post("/api/meta")
    assert_error(response, 405, 40000)
    assert "GET" in response.headers["allow"]


def test_validation_error_uses_envelope_without_echoing_input():
    app = app_for_test()

    @app.get("/test-validation")
    def validated(count: int):
        return count

    with TestClient(app) as client:
        response = client.get("/test-validation", params={"count": "private-input"})
    assert_error(response, 400, 40000)
    assert "private-input" not in response.text


def test_controlled_error_uses_its_business_code():
    app = app_for_test()

    @app.get("/test-denied")
    def denied():
        raise ApiError(40003, "没有访问权限", status_code=403)

    with TestClient(app) as client:
        response = client.get("/test-denied")
    assert_error(response, 403, 40003)
    assert response.json()["message"] == "没有访问权限"


def test_http_exception_does_not_echo_arbitrary_details():
    app = app_for_test()

    @app.get("/test-http-error")
    def http_error():
        raise HTTPException(403, detail={"password": "private-http-detail"})

    with TestClient(app) as client:
        response = client.get("/test-http-error")
    assert_error(response, 403, 40003)
    assert "private-http-detail" not in response.text


def test_unexpected_error_is_uniform_without_sensitive_response_or_log(caplog):
    app = app_for_test()

    @app.get("/test-crash")
    def crash():
        raise RuntimeError("private-error-detail")

    with caplog.at_level(logging.ERROR), TestClient(app) as client:
        response = client.get("/test-crash")
    assert_error(response, 500, 50000)
    assert "private-error-detail" not in response.text
    assert "private-error-detail" not in caplog.text
    assert "RuntimeError" in caplog.text
