import json
import re
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from server.backend.tests.test_accounts import application, login
from server.backend.tests.test_invites import create
from server.backend.tests.test_members import member, provision


@pytest.mark.parametrize("csrf", ["恶意", "é" * 43, "x" * 43])
def test_malformed_csrf_is_forbidden_not_internal_error(tmp_path, csrf):
    with TestClient(application(tmp_path)) as web:
        provision(web)
        assert web.post("/__logout", data={"csrf": csrf}).status_code == 403


def test_usage_all_is_membership_scoped_and_read_receipts_are_viewable(tmp_path):
    with TestClient(application(tmp_path)) as web:
        admin, user = provision(web)
        folder = tmp_path / "gateway"
        folder.mkdir()
        rows = [
            {"tenant": cube, "ts": "2026-10-26T00:00:00Z", "tool": "search_memory", "ok": True}
            for cube in ("cube-a", "cube-b")
        ]
        (folder / "calls-20261026.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )
        web.app.state.audit.config = web.app.state.settings.model_copy(
            update={"audit_gateway_dir": folder}
        )
        member(web, user, "viewer")
        login(web, "member", "member-password-123")
        response = web.post(
            "/api/dashboard/usage/query",
            json={"projectId": "all", "startDate": "2026-10-26", "endDate": "2026-10-26"},
        )
        assert response.json()["data"]["summary"]["totalCalls"] == 1
        assert (
            web.get(
                "/api/dashboard/project/memory/operation?projectId=a&operationId=missing"
            ).status_code
            == 404
        )


def test_concurrent_admin_demotions_keep_one_effective_admin(tmp_path):
    with TestClient(application(tmp_path)) as web:
        admin, user = provision(web)
        web.post(f"/admin/accounts/{user['id']}", json={"platform_admin": True})
        with TestClient(web.app) as other:
            login(other, "member", "member-password-123")
            pairs = [(web, admin), (other, user)]
            with ThreadPoolExecutor(max_workers=2) as pool:
                codes = list(
                    pool.map(
                        lambda pair: pair[0]
                        .post(f"/admin/accounts/{pair[1]['id']}", json={"platform_admin": False})
                        .status_code,
                        pairs,
                    )
                )
        assert sorted(codes) == [200, 409]


def test_invite_creator_deletion_preserves_invitation_and_clears_creator(tmp_path):
    with TestClient(application(tmp_path)) as web:
        admin, user = provision(web)
        member(web, user, "owner")
        web.post(f"/admin/accounts/{user['id']}", json={"platform_admin": True})
        # Transfer both seeded workspaces before deleting the old bootstrap owner.
        web.post("/admin/tenants/b/members", json={"account_id": user["id"], "role": "owner"})
        invitation = create(web)
        login(web, "member", "member-password-123")
        assert web.delete(f"/admin/accounts/{admin['id']}").status_code == 200
        with web.app.state.control.connection() as db:
            row = db.execute(
                "SELECT * FROM project_invites WHERE id=?", (invitation["id"],)
            ).fetchone()
            assert row["created_by_account_id"] is None
            assert (
                db.execute(
                    "SELECT count(*) FROM projects WHERE owner_account_id=?", (admin["id"],)
                ).fetchone()[0]
                == 0
            )
        assert web.get(invitation["path"]).status_code == 200


def test_existing_account_invite_login_continuation_is_local_only(tmp_path):
    with TestClient(application(tmp_path)) as web:
        provision(web)
        invitation = create(web)
        for next_path in (invitation["path"], "https://evil.example/"):
            page = web.get("/__login", params={"next": next_path})
            csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
            reply = web.post(
                "/__login",
                data={
                    "username": "member",
                    "password": "member-password-123",
                    "csrf": csrf,
                    "next": next_path,
                },
                follow_redirects=False,
            )
            assert reply.status_code == 303
            assert reply.headers["location"] == (
                next_path if next_path.startswith("/__login/invite/") else "/cn/projects"
            )
