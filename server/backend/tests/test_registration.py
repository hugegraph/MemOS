import re

from fastapi.testclient import TestClient
from server.backend.tests.test_accounts import application, login, seed


def register(web, **changes):
    page = web.get("/__login/register")
    assert page.status_code == 200
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
    return web.post(
        "/__login/register",
        data={
            "csrf": csrf,
            "username": "new-user",
            "password": "new-password-123",
            "confirm_password": "new-password-123",
            **changes,
        },
        follow_redirects=False,
    )


def test_registration_login_permissions_and_persistence(tmp_path):
    with TestClient(application(tmp_path)) as web:
        seed(web.app)
        assert 'href="/__login/register"' in web.get("/__login").text
        response = register(web)
        assert response.status_code == 303
        assert response.headers["location"] == "/__login?registered=1"
        assert "注册成功" in web.get(response.headers["location"]).text
        assert login(web, "new-user", "new-password-123").status_code == 303
        me = web.get("/api/auth/me").json()["data"]
        assert me["platformAdmin"] is False
        assert me["currentProjectId"].startswith("personal_")
        assert web.get("/admin/accounts").status_code == 403
        assert len(web.get("/api/dashboard/project/dict").json()["data"]) == 1
    with TestClient(application(tmp_path)) as web:
        assert login(web, "new-user", "new-password-123").status_code == 303


def test_registration_validation_and_duplicate_do_not_create_accounts(tmp_path):
    with TestClient(application(tmp_path)) as web:
        seed(web.app)
        for changes, status in [
            ({"confirm_password": "different"}, 400),
            ({"password": "short", "confirm_password": "short"}, 400),
            ({"username": "bad username"}, 400),
            ({"username": "ADMIN"}, 409),
            ({"platform_admin": "true"}, 400),
        ]:
            response = register(web, **changes)
            assert response.status_code == status
            assert "<form" in response.text and 'role="alert"' in response.text
            assert 'value="new-password-123"' not in response.text
        with web.app.state.control.connection() as db:
            assert db.execute("SELECT count(*) FROM accounts").fetchone()[0] == 1


def test_registration_rejects_csrf_and_cross_origin(tmp_path):
    with TestClient(application(tmp_path)) as web:
        seed(web.app)
        assert web.post("/__login/register", data={}).status_code == 400
        assert web.post("/__login/register", data={"csrf": "invalid"}).status_code == 403
        page = web.get("/__login/register")
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
        response = web.post(
            "/__login/register",
            headers={"Origin": "https://evil.invalid"},
            data={
                "csrf": csrf,
                "username": "new-user",
                "password": "new-password-123",
                "confirm_password": "new-password-123",
            },
        )
        assert response.status_code == 403
