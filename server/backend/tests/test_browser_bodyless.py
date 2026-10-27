from fastapi.testclient import TestClient
from server.backend.tests.test_accounts import login
from server.backend.tests.test_keys import application, provision

ORIGIN = {"Origin": "http://127.0.0.1:5274"}


def test_admin_browser_can_revoke_key_without_json_body(tmp_path):
    with TestClient(application(tmp_path)) as web:
        provision(web)
        key = web.post(
            "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "browser"}
        ).json()["data"]
        response = web.post("/api/dashboard/keys/revoke/" + key["id"], headers=ORIGIN)
        assert response.status_code == 200
        assert response.json()["code"] == 0
        assert (
            web.post("/api/dashboard/keys/list", json={"projectId": "a"}).json()["data"]["total"]
            == 0
        )


def test_empty_browser_posts_keep_origin_role_and_body_validation(tmp_path):
    with TestClient(application(tmp_path)) as web:
        _, user = provision(web)
        path = "/api/dashboard/keys/revoke/missing"
        assert web.post(path, headers={"Origin": "https://evil.invalid"}).status_code == 403
        assert web.post(path, headers=ORIGIN, content=b"{}").status_code == 403
        assert (
            web.post(path, headers={**ORIGIN, "Content-Length": "0"}, content=b"{}").status_code
            == 403
        )
        assert (
            web.post(
                path, headers={**ORIGIN, "Content-Type": "text/plain"}, content=b"{}"
            ).status_code
            == 403
        )
        assert web.post("/api/dashboard/keys/create", headers=ORIGIN).status_code == 400
        key = web.post(
            "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "protected"}
        ).json()["data"]
        web.post("/admin/tenants/a/members", json={"account_id": user["id"], "role": "viewer"})
        login(web, "member", "member-password-123")
        assert (
            web.post("/api/dashboard/keys/revoke/" + key["id"], headers=ORIGIN).status_code == 403
        )


def test_browser_delete_and_project_transitions_without_body(tmp_path):
    with TestClient(application(tmp_path)) as web:
        _, user = provision(web)
        response = web.delete("/admin/accounts/" + user["id"], headers=ORIGIN)
        assert response.status_code == 200
        project = web.post(
            "/api/dashboard/project/create", json={"name": "browser-project", "description": ""}
        ).json()["data"]
        assert (
            web.post("/api/dashboard/project/delete/" + project["id"], headers=ORIGIN).status_code
            == 200
        )
        assert (
            web.post("/api/dashboard/project/restore/" + project["id"], headers=ORIGIN).status_code
            == 200
        )
