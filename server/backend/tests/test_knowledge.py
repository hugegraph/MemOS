import json

import httpx
from fastapi.testclient import TestClient
from server.backend.app import create_app
from server.backend.tests.test_accounts import configured, login, seed
from server.backend.tests.test_audit_api import validate_contract

BASE = "/api/dashboard/knowledgebase/"


class KnowledgeTransport:
    def __init__(self):
        self.items = {}
        self.calls = []
        self.fail_delete = False

    def __call__(self, request):
        body = json.loads(request.content or b"{}")
        self.calls.append((request.url.path, body))
        cube, identifier = body.get("mem_cube_id"), body.get("knowledgebase_id")
        path = request.url.path.rsplit("/", 1)[-1]
        if path == "create_knowledgebase":
            identifier = "kb_" + str(len(self.items) + 1)
            row = {
                "id": identifier,
                "knowledgebase_id": identifier,
                "knowledgebase_name": body["knowledgebase_name"],
                "knowledgebase_description": body.get("knowledgebase_description", ""),
                "file_count": 0,
                "total_size": 0,
                "created_at": "2026-10-26T00:00:00Z",
                "updated_at": "2026-10-26T00:00:00Z",
                "secret": "never-project-me",
            }
            self.items[cube, identifier] = row
            return httpx.Response(200, json={"code": 0, "data": row})
        if identifier:
            row = self.items.get((cube, identifier))
            if row is None:
                return httpx.Response(200, json={"code": 404, "data": None})
            if path == "update_knowledgebase":
                row.update(
                    {
                        k: v
                        for k, v in body.items()
                        if k in ("knowledgebase_name", "knowledgebase_description")
                    }
                )
            if path == "delete_knowledgebase":
                if self.fail_delete:
                    return httpx.Response(503)
                del self.items[cube, identifier]
                return httpx.Response(200, json={"code": 0, "data": {"success": True}})
            return httpx.Response(200, json={"code": 0, "data": row})
        rows = [v for (tenant, _), v in self.items.items() if tenant == cube]
        size, page = body.get("page_size", 200), body.get("page", 1)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "knowledgebase_list": rows[(page - 1) * size : page * size],
                    "total": len(rows),
                },
            },
        )


def application(tmp_path, remote):
    return create_app(configured(tmp_path), transport=httpx.MockTransport(remote))


def create(web, **extra):
    response = web.post(
        BASE + "create", json={"knowledgebaseName": "kb-name", "projectId": "a", **extra}
    )
    assert response.status_code == 200, response.text
    validate_contract(response.json(), "KnowledgeBase")
    return response.json()["data"]


def test_entities_crud_filter_projection_and_restart(tmp_path):
    remote = KnowledgeTransport()
    with TestClient(application(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        kb = create(web)
        assert kb["createTime"] == kb["updateTime"] == "2026-10-26 08:00:00"
        identifier = kb["kbId"]
        assert kb["projectIds"] == ["a"] and "secret" not in str(kb)
        response = web.post(BASE + "list", json={"projectId": "a", "name": "kb-", "pageSize": 1})
        validate_contract(response.json(), "KnowledgeBaseRecords")
        assert response.json()["data"]["total"] == 1
        assert web.post(BASE + "list", json={"projectId": "b"}).json()["data"]["total"] == 0
        assert (
            web.post(
                BASE + "update", json={"id": identifier, "knowledgebaseName": "edited"}
            ).json()["data"]["knowledgebaseName"]
            == "edited"
        )
        cookies = dict(web.cookies)
    with TestClient(application(tmp_path, remote)) as web:
        web.cookies.update(cookies)
        assert (
            web.post(BASE + "detail", json={"knowledgeBaseId": identifier}).json()["data"][
                "knowledgebaseName"
            ]
            == "edited"
        )
        assert web.post(
            BASE + "delete", json={"projectId": "a", "knowledgeBaseId": identifier}
        ).json()["data"] == {"ok": True, "id": identifier}
        assert web.post(BASE + "detail", json={"knowledgeBaseId": identifier}).status_code == 404


def test_association_full_replacement_sharing_and_role_boundaries(tmp_path):
    remote = KnowledgeTransport()
    with TestClient(application(tmp_path, remote)) as admin:
        seed(admin.app)
        user = admin.app.state.identity.create_account("reader", "reader-password-123")
        login(admin)
        admin.post("/admin/tenants/b/members", json={"account_id": user["id"], "role": "viewer"})
        first, second = create(admin), create(admin, knowledgebaseName="second")
        with TestClient(admin.app) as reader:
            login(reader, "reader", "reader-password-123")
            assert (
                reader.post(BASE + "detail", json={"knowledgeBaseId": first["kbId"]}).status_code
                == 404
            )
            assert (
                reader.post(
                    BASE + "project/associate",
                    json={"projectId": "b", "knowledgeBaseIds": [first["kbId"]]},
                ).status_code
                == 403
            )
            assert (
                admin.post(
                    BASE + "project/associate",
                    json={"projectId": "b", "knowledgeBaseIds": [first["kbId"], second["kbId"]]},
                ).status_code
                == 200
            )
            view = reader.post(BASE + "detail", json={"knowledgeBaseId": first["kbId"]}).json()[
                "data"
            ]
            assert view["projectIds"] == ["b"]
            assert (
                reader.post(
                    BASE + "update", json={"id": first["kbId"], "knowledgebaseName": "denied"}
                ).status_code
                == 403
            )
            admin.post(
                BASE + "project/associate",
                json={"projectId": "b", "knowledgeBaseIds": [second["kbId"]]},
            )
            assert (
                reader.post(BASE + "detail", json={"knowledgeBaseId": first["kbId"]}).status_code
                == 404
            )
            assert reader.post(BASE + "list", json={"projectId": "b"}).json()["data"]["total"] == 1
        assert (
            admin.post(
                BASE + "project/remove", json={"projectId": "b", "knowledgeBaseId": second["kbId"]}
            ).status_code
            == 200
        )
        assert remote.items["cube-a", second["kbId"]]


def test_invalid_inputs_and_failed_delete_remains_retryable(tmp_path):
    remote = KnowledgeTransport()
    with TestClient(application(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        for body in (
            {"knowledgebaseName": " "},
            {"knowledgebaseName": "ok", "projectIds": ["a", "foreign"]},
            {"knowledgebaseName": "ok", "user_name": "foreign"},
        ):
            assert web.post(BASE + "create", json=body).status_code in {400, 404}
        identifier = create(web)["kbId"]
        remote.fail_delete = True
        assert (
            web.post(
                BASE + "delete", json={"projectId": "a", "knowledgeBaseId": identifier}
            ).status_code
            == 503
        )
        assert web.post(BASE + "detail", json={"knowledgeBaseId": identifier}).status_code == 409
        remote.fail_delete = False
        assert (
            web.post(
                BASE + "delete", json={"projectId": "a", "knowledgeBaseId": identifier}
            ).status_code
            == 200
        )
