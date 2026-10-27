from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from server.backend.tests.test_accounts import application, login, seed

BASE = "/admin/tenants/a/"


def provision(web):
    admin = seed(web.app)
    user = web.app.state.identity.create_account("member", "member-password-123")
    login(web)
    return admin, user


def member(web, user, role):
    return web.post(BASE + "members", json={"account_id": user["id"], "role": role})


def test_candidates_roles_add_change_remove_and_leave(tmp_path):
    with TestClient(application(tmp_path)) as web:
        admin, user = provision(web)
        assert (
            web.get(BASE + "member-candidates?query=member").json()["data"]["records"][0]["member"]
            is False
        )
        assert member(web, user, "viewer").status_code == 200
        candidates = web.get(BASE + "member-candidates?query=member").json()["data"]["records"]
        assert candidates[0]["member"] is True
        assert member(web, user, "editor").json()["data"]["role"] == "editor"
        assert len(web.get(BASE + "members").json()["data"]["records"]) == 2
        assert web.post(BASE + f"members/{user['id']}/remove", json={}).status_code == 200
        assert member(web, user, "viewer").status_code == 200
        login(web, "member", "member-password-123")
        assert web.get(BASE + "member-candidates?query=a").status_code == 403
        assert web.get(BASE + "members").status_code == 200
        assert web.post(BASE + "members/leave", json={}).status_code == 200
        assert web.get(BASE + "members").status_code == 404


def test_viewer_editor_owner_data_permissions_and_selection(tmp_path):
    with TestClient(application(tmp_path)) as web:
        admin, user = provision(web)
        member(web, user, "viewer")
        login(web, "member", "member-password-123")
        projects = web.get("/api/auth/me").json()["data"]["projects"]
        assert "a" in {p["id"] for p in projects} and "b" not in {p["id"] for p in projects}
        assert (
            web.post(
                "/api/dashboard/project/update", json={"id": "a", "status": "ENABLED"}
            ).status_code
            == 200
        )
        assert web.get("/api/auth/me").json()["data"]["currentProjectId"] == "a"
        delete_body = {"projectId": "a", "memory_ids": ["m"], "memType": "text_mem"}
        assert web.post("/api/dashboard/project/memory/delete", json=delete_body).status_code == 403
        assert (
            web.post(
                "/api/dashboard/project/memory/list",
                json={"projectId": "b", "memoryDetailType": "text_mem"},
            ).status_code
            == 404
        )
        assert (
            web.post(
                "/api/dashboard/project/update", json={"id": "a", "name": "forbidden"}
            ).status_code
            == 403
        )
        assert member(web, admin, "viewer").status_code == 403
        login(web)
        member(web, user, "editor")
        login(web, "member", "member-password-123")
        # The transport returns500; editor gets past authorization into upstream,
        # while viewer was rejected before any upstream request.
        assert web.post("/api/dashboard/project/memory/delete", json=delete_body).status_code == 502
        assert web.post("/api/dashboard/project/delete/a").status_code == 403


def test_effective_last_owner_guards_members_account_disable_delete(tmp_path):
    with TestClient(application(tmp_path)) as web:
        admin, user = provision(web)
        assert member(web, admin, "viewer").status_code == 409
        assert web.post(BASE + "members/leave", json={}).status_code == 409
        assert web.post(BASE + f"members/{admin['id']}/remove", json={}).status_code == 409
        member(web, user, "owner")
        member(web, admin, "viewer")
        assert web.post(f"/admin/accounts/{user['id']}", json={"disabled": True}).status_code == 409
        assert web.delete(f"/admin/accounts/{user['id']}").status_code == 409
        member(web, admin, "owner")
        assert web.post(f"/admin/accounts/{user['id']}", json={"disabled": True}).status_code == 200
        assert member(web, admin, "viewer").status_code == 409


def test_concurrent_owner_removals_leave_one_effective_owner(tmp_path):
    with TestClient(application(tmp_path)) as web:
        admin, user = provision(web)
        member(web, user, "owner")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(lambda person: member(web, person, "viewer").status_code, [admin, user])
            )
        assert sorted(results) == [200, 409]


def test_member_validation_personal_space_and_revocation(tmp_path):
    with TestClient(application(tmp_path)) as web:
        admin, user = provision(web)
        for body in (
            {"role": "owner"},
            {"role": "root", "account_id": user["id"]},
            {"role": "viewer", "account_id": user["id"], "username": "wrong"},
        ):
            assert web.post(BASE + "members", json=body).status_code == 400
        personal = "personal_" + user["id"]
        assert (
            web.post(
                f"/admin/tenants/{personal}/members", json={"username": "admin", "role": "owner"}
            ).status_code
            == 409
        )
        member(web, user, "viewer")
        with TestClient(web.app) as other:
            login(other, "member", "member-password-123")
            assert (
                other.post(
                    "/api/dashboard/project/update", json={"id": "a", "status": "ENABLED"}
                ).status_code
                == 200
            )
            web.post(BASE + f"members/{user['id']}/remove", json={})
            assert other.post("/api/dashboard/log/list", json={"projectId": "a"}).status_code == 404
            assert other.get("/api/auth/me").json()["data"]["currentProjectId"] != "a"
