import re
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from server.backend.tests.test_accounts import application, login
from server.backend.tests.test_members import provision

BASE = "/admin/tenants/a/invites"


def create(web, role="viewer", **extra):
    response = web.post(BASE, json={"role": role, **extra})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def accept(web, path, **extra):
    page = web.get(path)
    assert page.status_code == 200, page.text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
    return web.post(path, data={"csrf": csrf, **extra}, follow_redirects=False)


def test_invitation_create_role_list_digest_and_single_use(tmp_path):
    with TestClient(application(tmp_path)) as web:
        admin, user = provision(web)
        invitation = create(web)
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", invitation["expiresAt"])
        path, identifier = invitation["path"], invitation["id"]
        token = path.rsplit("/", 1)[1]
        assert token not in web.get(BASE).text
        with web.app.state.control.connection() as db:
            assert token not in str([tuple(r) for r in db.execute("SELECT * FROM project_invites")])
        assert web.post(BASE + f"/{identifier}/role", json={"role": "editor"}).status_code == 200
        login(web, "member", "member-password-123")
        assert accept(web, path).status_code == 303
        assert web.get("/api/auth/me").json()["data"]["currentProjectId"] == "a"
        assert web.get("/admin/tenants/a/members").json()["data"]["records"][1]["role"] == "editor"
        assert web.post(path, data={"csrf": ""}).status_code == 403
        assert web.get(path).status_code == 409


def test_revoked_expired_disabled_workspace_and_cross_tenant(tmp_path):
    with TestClient(application(tmp_path)) as web:
        provision(web)
        revoked = create(web)
        assert web.post(BASE + f"/{revoked['id']}/revoke", json={}).status_code == 200
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", revoked["expiresAt"])
        assert web.get(revoked["path"]).status_code == 409
        expired = create(web, expiresIn="1s")
        with web.app.state.control.connection() as db:
            db.execute("UPDATE project_invites SET expires_at=0 WHERE id=?", (expired["id"],))
        assert web.get(expired["path"]).status_code == 410
        assert web.get(BASE).json()["data"]["records"][0]["status"] in {"expired", "revoked"}
        revoked_row = next(
            row for row in web.get(BASE).json()["data"]["records"] if row["id"] == revoked["id"]
        )
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", revoked_row["revokedAt"])
        assert (
            web.post(
                f"/admin/tenants/b/invites/{revoked['id']}/role", json={"role": "owner"}
            ).status_code
            == 404
        )
        active = create(web)
        web.post("/api/dashboard/project/update", json={"id": "a", "status": "DISABLED"})
        assert web.get(active["path"]).status_code == 409


def test_signup_accept_and_no_partial_account_on_reused_invite(tmp_path):
    with TestClient(application(tmp_path)) as admin:
        provision(admin)
        invitation = create(admin, "owner")
        with TestClient(admin.app) as anonymous:
            assert (
                anonymous.post(
                    invitation["path"], data={"username": "joined", "password": "new-password-123"}
                ).status_code
                == 403
            )
            assert (
                accept(
                    anonymous, invitation["path"], username="joined", password="new-password-123"
                ).status_code
                == 303
            )
            me = anonymous.get("/api/auth/me").json()["data"]
            assert me["username"] == "joined" and me["currentProjectId"] == "a"
            assert next(p for p in me["projects"] if p["id"] == "a")["role"] == "owner"
        assert admin.get("/admin/accounts?query=joined").json()["data"]["total"] == 1


def test_existing_member_not_downgraded_and_role_validation(tmp_path):
    with TestClient(application(tmp_path)) as web:
        admin, user = provision(web)
        for body in (
            {"role": "admin"},
            {"role": "viewer", "expiresIn": "0h"},
            {"role": "viewer", "expiresIn": "999999h"},
        ):
            assert web.post(BASE, json=body).status_code == 400
        invitation = create(web)
        assert accept(web, invitation["path"]).status_code == 409
        assert web.get(BASE).json()["data"]["records"][0]["status"] == "active"
        login(web, "member", "member-password-123")
        assert web.get(BASE).status_code == 404


def test_concurrent_accept_only_one_membership_created(tmp_path):
    with TestClient(application(tmp_path)) as admin:
        provision(admin)
        invitation = create(admin)
        users = [
            admin.app.state.identity.create_account(f"join{i}", "password-for-join-123")
            for i in range(2)
        ]
        clients = [TestClient(admin.app), TestClient(admin.app)]
        for i, web in enumerate(clients):
            web.__enter__()
            login(web, f"join{i}", "password-for-join-123")
        try:
            forms = [
                re.search(r'name="csrf" value="([^"]+)"', web.get(invitation["path"]).text)[1]
                for web in clients
            ]
            with ThreadPoolExecutor(max_workers=2) as pool:
                codes = list(
                    pool.map(
                        lambda i: clients[i]
                        .post(invitation["path"], data={"csrf": forms[i]}, follow_redirects=False)
                        .status_code,
                        range(2),
                    )
                )
            assert sorted(codes) == [303, 409]
            with admin.app.state.control.connection() as db:
                assert (
                    db.execute(
                        (
                            "SELECT count(*) FROM memberships WHERE tenant_id='a' "
                            "AND account_id IN (?,?)"
                        ),
                        tuple(u["id"] for u in users),
                    ).fetchone()[0]
                    == 1
                )
        finally:
            for web in clients:
                web.__exit__(None, None, None)
