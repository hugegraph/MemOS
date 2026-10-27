"""The admin URL namespace includes both platform and project administration."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from server.backend.tests.test_accounts import application, login
from server.backend.tests.test_members import member, provision


def project_operations(tenant, target, invitation):
    base = f"/admin/tenants/{tenant}"
    return [
        ("GET", base + "/members", None, "viewer"),
        ("GET", base + "/member-candidates", None, "owner"),
        ("POST", base + "/members", {"account_id": target, "role": "viewer"}, "owner"),
        ("POST", base + f"/members/{target}/remove", {}, "owner"),
        ("GET", base + "/invites", None, "owner"),
        ("POST", base + "/invites", {"role": "viewer", "expiresIn": "24h"}, "owner"),
        ("POST", base + f"/invites/{invitation}/role", {"role": "editor"}, "owner"),
        ("POST", base + f"/invites/{invitation}/revoke", {}, "owner"),
        ("POST", base + "/members/leave", {}, "viewer"),
    ]


@pytest.mark.parametrize("role", ["viewer", "editor", "owner", None])
def test_nonplatform_roles_project_management_and_foreign_project(tmp_path, role):
    with TestClient(application(tmp_path)) as web:
        admin, user = provision(web)
        target = web.app.state.identity.create_account("candidate", "candidate-password-123")
        invitation = web.post(
            "/admin/tenants/a/invites", json={"role": "viewer", "expiresIn": "24h"}
        ).json()["data"]["id"]
        if role:
            assert member(web, user, role).status_code == 200
        login(web, "member", "member-password-123")
        assert web.get("/api/auth/me").json()["data"]["platformAdmin"] is False
        for method, path, body in [
            ("GET", "/admin/accounts", None),
            ("POST", f"/admin/accounts/{admin['id']}", {"disabled": False}),
            ("DELETE", f"/admin/accounts/{admin['id']}", None),
        ]:
            assert web.request(method, path, json=body).status_code == 403
        # Even an owner of A has no access to B. No operation may alter B.
        for method, path, body, _ in project_operations("b", target["id"], invitation):
            assert web.request(method, path, json=body).status_code == 404
        for method, path, body, minimum in project_operations("a", target["id"], invitation):
            expected = (
                404 if role is None else 200 if minimum == "viewer" or role == "owner" else 403
            )
            assert web.request(method, path, json=body).status_code == expected, (method, path)
        # The owner test leaves after exercising management; the seeded owner remains.
        login(web)
        members = web.get("/admin/tenants/a/members").json()["data"]["records"]
        assert admin["id"] in {row["account_id"] for row in members}


def test_anonymous_management_routes_require_session(tmp_path):
    with TestClient(application(tmp_path)) as web:
        operations = project_operations("a", "target", "invitation")
        operations.extend(
            [
                ("GET", "/api/auth/me", None, "session"),
                ("GET", "/admin/accounts", None, "platform"),
                ("POST", "/admin/accounts/target", {"disabled": True}, "platform"),
                ("DELETE", "/admin/accounts/target", None, "platform"),
            ]
        )
        for method, path, body, _ in operations:
            assert web.request(method, path, json=body).status_code == 401


def test_spec_documents_every_platform_and_project_permission():
    spec = json.loads((Path(__file__).parents[3] / "openapi.json").read_text(encoding="utf-8"))
    for path, operations in spec["paths"].items():
        if not path.startswith("/admin/") and path != "/api/auth/me":
            continue
        for method, operation in operations.items():
            if method not in {"get", "post", "delete"}:
                continue
            policy = operation["x-authorization"]
            assert policy["loginRequired"] is True
            if path == "/api/auth/me":
                assert policy["scope"] == "session"
            elif path.startswith("/admin/accounts"):
                assert policy["scope"] == "platform" and policy["platformAdminRequired"] is True
            else:
                expected = (
                    "viewer"
                    if path.endswith(("/members/leave", "/members"))
                    and (method == "get" or path.endswith("/leave"))
                    else "owner"
                )
                assert policy["scope"] == "project"
                assert policy["minimumRole"] == expected
                assert policy["platformAdminOverride"] is True
                assert policy["selfOnly"] == path.endswith("/members/leave")
