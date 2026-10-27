from fastapi.testclient import TestClient
from server.backend.tests.test_accounts import application, login
from server.backend.tests.test_audit_api import validate_contract
from server.backend.tests.test_members import provision


def test_authenticated_accounts_members_and_invites_match_root_contract(tmp_path):
    with TestClient(application(tmp_path)) as web:
        admin, user = provision(web)
        validate_contract(web.get("/api/auth/me").json(), "Account")
        validate_contract(web.get("/admin/accounts").json(), "AdminAccountRecords")
        validate_contract(
            web.post(f"/admin/accounts/{user['id']}", json={"display_name": "Edited"}).json(),
            "AdminAccount",
        )
        prefix = "/admin/tenants/a/"
        validate_contract(web.get(prefix + "members").json(), "ProjectMemberRecords")
        validate_contract(
            web.get(prefix + "member-candidates?query=member").json(), "MemberCandidateRecords"
        )
        validate_contract(
            web.post(prefix + "members", json={"account_id": user["id"], "role": "viewer"}).json(),
            "MemberSet",
        )
        created = web.post(prefix + "invites", json={"role": "editor"}).json()
        validate_contract(created, "CreatedInvite")
        identifier = created["data"]["id"]
        validate_contract(web.get(prefix + "invites").json(), "ProjectInviteRecords")
        validate_contract(
            web.post(prefix + f"invites/{identifier}/role", json={"role": "viewer"}).json(),
            "InviteRole",
        )
        validate_contract(web.post(prefix + f"invites/{identifier}/revoke", json={}).json(), "Ok")
        # A real non-admin owner receives owner permission from membership, not platformAdmin.
        web.post(prefix + "members", json={"account_id": user["id"], "role": "owner"})
        login(web, "member", "member-password-123")
        assert web.get(prefix + "member-candidates?query=admin").status_code == 200
        assert web.post(prefix + "invites", json={"role": "viewer"}).status_code == 200
        assert web.get("/admin/accounts").status_code == 403
