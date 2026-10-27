import re

import httpx
from fastapi.testclient import TestClient
from server.backend.app import create_app
from server.backend.settings import Settings


def configured(tmp_path):
    return Settings(
        data_dir=tmp_path / "data",
        projects_seed=(("a", "cube-a"), ("b", "cube-b")),
        default_project_id="a",
        auth_enabled=True,
    )


def application(tmp_path):
    return create_app(
        configured(tmp_path), transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )


def seed(app):
    return app.state.identity.create_account(
        "admin", "correct-password-123", admin=True, bootstrap=True
    )


def login(web, username="admin", password="correct-password-123"):
    page = web.get("/__login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
    return web.post(
        "/__login",
        data={"username": username, "password": password, "csrf": csrf},
        follow_redirects=False,
    )


def test_login_session_logout_and_response_projection(tmp_path):
    with TestClient(application(tmp_path)) as web:
        seed(web.app)
        assert web.get("/api/auth/me").status_code == 401
        assert web.get("/admin/accounts").status_code == 401
        assert login(web, password="wrong").status_code == 401
        assert login(web).status_code == 303
        assert "HttpOnly" in login(web).headers["set-cookie"]
        me = web.get("/api/auth/me").json()["data"]
        assert me["platformAdmin"] is True and me["currentProjectId"] == "a"
        accounts = web.get("/admin/accounts").json()["data"]
        assert accounts["total"] == 1
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", accounts["records"][0]["created_at"]
        )
        assert "password" not in str(accounts) and "token_hash" not in str(accounts)
        assert web.get("/__logout").status_code == 200
        page = web.get("/__logout")
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
        assert web.post("/__logout", data={"csrf": csrf}, follow_redirects=False).status_code == 303
        assert web.get("/api/auth/me").status_code == 401


def test_effective_last_admin_disable_delete_and_session_revocation(tmp_path):
    with TestClient(application(tmp_path)) as web:
        admin = seed(web.app)
        user = web.app.state.identity.create_account("other", "other-password-123", admin=True)
        login(web)
        assert (
            web.post(f"/admin/accounts/{admin['id']}", json={"platform_admin": False}).status_code
            == 200
        )
        assert web.get("/admin/accounts").status_code == 403
        login(web, "other", "other-password-123")
        assert web.post(f"/admin/accounts/{user['id']}", json={"disabled": True}).status_code == 409
        assert web.delete(f"/admin/accounts/{user['id']}").status_code == 409
        assert (
            web.post(f"/admin/accounts/{admin['id']}", json={"platform_admin": True}).status_code
            == 200
        )
        assert web.post(f"/admin/accounts/{user['id']}", json={"disabled": True}).status_code == 200
        assert web.get("/api/auth/me").status_code == 401
        assert login(web, "other", "other-password-123").status_code == 401


def test_session_persists_restart_expires_and_origin_and_csrf(tmp_path):
    with TestClient(application(tmp_path)) as web:
        seed(web.app)
        assert (
            web.post(
                "/__login", data={"username": "admin", "password": "correct-password-123"}
            ).status_code
            == 403
        )
        login(web)
        cookies = dict(web.cookies)
        assert (
            web.post(
                "/api/dashboard/project/create",
                json={"name": "evil", "description": ""},
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
    with TestClient(application(tmp_path)) as web:
        web.cookies.update(cookies)
        assert web.get("/api/auth/me").status_code == 200
        with web.app.state.control.connection() as db:
            db.execute("UPDATE sessions SET expires_at=0")
        assert web.get("/api/auth/me").status_code == 401


def test_account_validation_paging_and_delete_relationships(tmp_path):
    with TestClient(application(tmp_path)) as web:
        seed(web.app)
        other = web.app.state.identity.create_account("other", "other-password-123")
        login(web)
        assert web.get("/admin/accounts?page=0").status_code == 400
        assert web.get("/admin/accounts?query=other").json()["data"]["total"] == 1
        for body in ({}, {"disabled": "false"}, {"password": "injected"}, {"display_name": None}):
            assert web.post(f"/admin/accounts/{other['id']}", json=body).status_code == 400
        assert web.delete(f"/admin/accounts/{other['id']}").status_code == 200
        assert web.get("/admin/accounts?query=other").json()["data"]["total"] == 0


def test_login_form_retains_same_origin_for_browser_post(tmp_path):
    with TestClient(application(tmp_path)) as web:
        response = web.get("/__login")
        assert response.headers["referrer-policy"] == "same-origin"
        assert "form-action 'self'" in response.headers["content-security-policy"]


def test_control_storage_failure_is_uniform_unavailable(tmp_path):
    import sqlite3

    with TestClient(application(tmp_path)) as web:
        seed(web.app)
        login(web)

        def fail(*args):
            raise sqlite3.OperationalError("private storage failure")

        web.app.state.identity.account_list = fail
        response = web.get("/admin/accounts")
        assert response.status_code == 503
        assert response.json()["code"] == 50300
        assert "private" not in response.text


def test_explicit_https_console_origin_and_secure_cookie(tmp_path):
    from server.backend.app import create_app

    config = configured(tmp_path).model_copy(
        update={"allowed_origins": ("https://console.example",)}
    )
    with TestClient(create_app(config), base_url="https://console.example") as web:
        seed(web.app)
        page = web.get("/__login")
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
        response = web.post(
            "/__login",
            data={"username": "admin", "password": "correct-password-123", "csrf": csrf},
            headers={"Origin": "https://console.example"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "Secure" in response.headers["set-cookie"]
