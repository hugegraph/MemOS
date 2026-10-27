import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from server.backend.app import create_app
from server.backend.tests.test_accounts import configured, login, seed
from server.backend.tests.test_audit_api import validate_contract

BASE = "/api/dashboard/profileTemplate/"
TREE = {"工作": {"职责": {"value": "工程师", "algorithm_updatable": False}}}


class TemplatesTransport:
    def __init__(self):
        self.rows = {}
        self.calls = []
        self.failure = None

    def __call__(self, request):
        body = json.loads(request.content)
        self.calls.append((request.url.path, body))
        if self.failure:
            return httpx.Response(200, json=self.failure)
        path = request.url.path.rsplit("/", 1)[-1]
        cube = body["mem_cube_id"]
        identifier = body.get("profile_template_id")
        row = self.rows.get((cube, identifier))
        if path == "add_profile_template":
            identifier = "tpl_" + str(len(self.rows) + 1)
            self.rows[cube, identifier] = {
                "id": identifier,
                "profile_template_id": identifier,
                "name": body["name"],
                "metadata": copy.deepcopy(body["metadata"]),
                "deprecated_paths": [],
                "bound_count": 0,
                "updated_at": "2026-10-26T18:30:02Z",
                "secret": "HIDDEN",
            }
            data = {"profile_template_id": identifier, "id": identifier}
        elif path == "list_profile_template":
            rows = [v for (tenant, _), v in self.rows.items() if tenant == cube]
            start = (body["page"] - 1) * body["page_size"]
            data = {
                "profile_template_list": rows[start : start + body["page_size"]],
                "total": len(rows),
            }
        elif row is None:
            return httpx.Response(200, json={"code": 404, "data": None})
        elif path == "get_profile_template":
            data = {**row, "profile_template": row}
        elif path == "update_profile_template":
            previous = row["metadata"]
            row.update(name=body["name"], metadata=copy.deepcopy(body["metadata"]))
            row["deprecated_paths"] = []
            for group, fields in previous.items():
                for key, leaf in fields.items():
                    if key not in body["metadata"].get(group, {}):
                        row["metadata"].setdefault(group, {})[key] = {**leaf, "deprecated": True}
                        row["deprecated_paths"].append(group + "." + key)
            data = {**row, "success": True}
        elif path == "delete_profile_template":
            del self.rows[cube, identifier]
            data = {"success": True, "profile_template_id": identifier}
        else:
            return httpx.Response(500)
        return httpx.Response(200, json={"code": 0, "data": data})


def application(tmp_path, remote):
    return create_app(configured(tmp_path), transport=httpx.MockTransport(remote))


def test_template_crud_contract_isolation_pagination_and_restart(tmp_path):
    remote = TemplatesTransport()
    with TestClient(application(tmp_path, remote)) as web:
        seed(web.app)
        assert web.post(BASE + "list", json={"projectId": "a"}).status_code == 401
        login(web)
        empty = web.post(BASE + "list", json={"projectId": "a"}).json()
        assert empty["data"] == {"list": [], "current": 1, "size": 10, "total": 0, "pages": 0}
        body = {"projectId": "a", "templateName": "template", **TREE}
        created = web.post(BASE + "create", json=body)
        assert created.status_code == 200, created.text
        validate_contract(created.json(), "Ok")
        identifier = created.json()["data"]["templateId"]
        params = {"projectId": "a", "templateId": identifier}
        detail = web.get(BASE + "detail", params=params)
        validate_contract(detail.json(), "ProfileTemplate")
        assert detail.json()["data"] == {
            **body,
            "templateId": identifier,
            "bindingCount": 0,
            "updatedAt": "2026-10-27 02:30:02",
        }
        assert "HIDDEN" not in detail.text
        listing = web.post(BASE + "list", json={"projectId": "a", "pageSize": 1})
        validate_contract(listing.json(), "ProfileTemplateList")
        assert listing.json()["data"]["list"][0] == detail.json()["data"]
        assert (
            web.post(BASE + "list", json={"projectId": "a", "pageSize": 1, "pageNum": 2}).json()[
                "data"
            ]["list"]
            == []
        )
        foreign = {**params, "projectId": "b"}
        assert web.get(BASE + "detail", params=foreign).status_code == 404
        assert web.post(BASE + "update", json={**body, **foreign}).status_code == 404
        assert web.post(BASE + "delete", json=foreign).status_code == 404
        assert (
            web.post(
                BASE + "update", json={**body, **params, "templateName": "renamed"}
            ).status_code
            == 200
        )
    with TestClient(application(tmp_path, remote)) as web:
        login(web)
        assert web.get(BASE + "detail", params=params).json()["data"]["templateName"] == "renamed"
        assert web.post(BASE + "delete", json=params).status_code == 200
        assert web.get(BASE + "detail", params=params).status_code == 404


@pytest.mark.parametrize(
    "changes",
    [
        {"templateName": " "},
        {"templateId": "forged"},
        {"bindingCount": 10},
        {"bad": {"field": {"value": 3}}},
        {"bad": {"field": {"algorithm_updatable": "false"}}},
        {"bad": {"value": {"value": "ambiguous"}}},
        {"bad": {}},
        {"bad": {"field": {"value": "x", "secret": "extra"}}},
    ],
)
def test_invalid_tree_rejected_before_upstream(tmp_path, changes):
    remote = TemplatesTransport()
    with TestClient(application(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        assert (
            web.post(
                BASE + "create", json={"projectId": "a", "templateName": "x", **TREE, **changes}
            ).status_code
            == 400
        )
        assert remote.calls == []


def test_native_deprecated_fields_excluded_without_mutating_source():
    from server.backend.profile_templates import view

    row = {
        "profile_template_id": "tpl_one",
        "name": "one",
        "bound_count": 0,
        "metadata": {
            "Work": {
                "active": {"value": "x", "algorithm_updatable": False},
                "old": {"value": "old", "algorithm_updatable": True, "deprecated": True},
            }
        },
        "deprecated_paths": ["Work.old"],
    }
    before = copy.deepcopy(row)
    assert view(row, "a")["Work"] == {"active": {"value": "x", "algorithm_updatable": False}}
    assert row == before


def test_native_null_default_is_optional_string_not_failed_list():
    from server.backend.profile_templates import view

    row = {
        "profile_template_id": "tpl_one",
        "name": "one",
        "bound_count": 1,
        "metadata": {"Work": {"role": {"value": None, "algorithm_updatable": True}}},
    }
    result = view(row, "a")
    assert result["Work"]["role"] == {"algorithm_updatable": True}
    validate_contract({"code": 0, "data": result, "message": "success"}, "ProfileTemplate")
    assert row["metadata"]["Work"]["role"]["value"] is None


def test_role_scope_upstream_errors_and_projection(tmp_path):
    remote = TemplatesTransport()
    with TestClient(application(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        user = web.app.state.identity.create_account("reader", "reader-password-123")
        web.post("/admin/tenants/a/members", json={"account_id": user["id"], "role": "viewer"})
        login(web, "reader", "reader-password-123")
        assert web.post(BASE + "list", json={"projectId": "a"}).status_code == 200
        before = len(remote.calls)
        assert (
            web.post(
                BASE + "create", json={"projectId": "a", "templateName": "x", **TREE}
            ).status_code
            == 403
        )
        assert web.post(BASE + "list", json={"projectId": "b"}).status_code == 404
        assert web.post(BASE + "list", json={"projectId": "cube-a"}).status_code == 404
        assert len(remote.calls) == before
        for failure in [
            {"code": 0, "data": {"profile_template_list": [], "total": -1}},
            {"code": 0, "data": {"profile_template_list": [], "total": 1}},
            {"code": 17, "data": {}},
            {"code": 0, "data": None},
        ]:
            remote.failure = failure
            assert web.post(BASE + "list", json={"projectId": "a"}).status_code == 502
