import base64
import hashlib
import json
import re

import httpx
from fastapi.testclient import TestClient
from server.backend.tests.test_accounts import login, seed
from server.backend.tests.test_audit_api import validate_contract
from server.backend.tests.test_knowledge import BASE, KnowledgeTransport, application, create


class FilesTransport(KnowledgeTransport):
    def __init__(self):
        super().__init__()
        self.files = {}

    def __call__(self, request):
        body = json.loads(request.content or b"{}")
        path = request.url.path.rsplit("/", 1)[-1]
        if "knowledgebase_file" not in path:
            return super().__call__(request)
        cube = body["mem_cube_id"]
        if path == "add_knowledgebase_file":
            item = body["file"][0]
            raw = base64.b64decode(item["base64"])
            identifier = "file_" + str(len(self.files) + 1)
            row = {
                "id": identifier,
                "knowledgebase_id": body["knowledgebase_id"],
                "name": item["name"],
                "size": len(raw),
                "status": "stored",
                "type": "document",
                "created_at": "2026-10-26T00:00:00Z",
                "storage_path": "private/not-for-api",
                "raw": raw,
            }
            self.files[cube, identifier] = row
            return httpx.Response(
                200, json={"code": 0, "data": [{k: v for k, v in row.items() if k != "raw"}]}
            )
        rows = [
            row
            for (tenant, identifier), row in self.files.items()
            if tenant == cube
            and (not body.get("file_ids") or identifier in body["file_ids"])
            and (
                not body.get("knowledgebase_id")
                or row["knowledgebase_id"] == body["knowledgebase_id"]
            )
        ]
        if path == "get_knowledgebase_file":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "file_detail_list": [
                            {k: v for k, v in r.items() if k != "raw"} for r in rows
                        ],
                        "total": len(rows),
                    },
                },
            )
        if path == "download_knowledgebase_file":
            row = self.files.get((cube, body["file_id"]))
            return httpx.Response(200, content=row["raw"]) if row else httpx.Response(404)
        if path == "delete_knowledgebase_file":
            for row in rows:
                del self.files[cube, row["id"]]
            return httpx.Response(
                200, json={"code": 0, "data": {"success": bool(rows), "deleted": len(rows)}}
            )
        return httpx.Response(500)


def upload(web, kb, content, name="original.txt"):
    return web.post(
        BASE + "file/add",
        json={
            "projectId": "a",
            "knowledgeBaseId": kb,
            "file": {"name": name, "content": content, "type": "text/plain"},
        },
    )


def test_file_time_filters_use_source_instant_not_display(tmp_path):
    remote = FilesTransport()
    with TestClient(application(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        kb = create(web)["kbId"]
        assert upload(web, kb, "sample").status_code == 200
        for bounds, total in [
            ({"startTime": "2026-10-26", "endTime": "2026-10-26"}, 1),
            ({"endTime": "2026-10-26T00:00:00Z"}, 1),
            ({"startTime": "2026-10-26T00:00:01Z"}, 0),
            ({"startTime": "2026-10-26T08:00:00+08:00"}, 1),
        ]:
            response = web.post(
                BASE + "file/list", json={"projectId": "a", "knowledgeBaseId": kb, **bounds}
            )
            assert response.status_code == 200
            data = response.json()["data"]
            assert data["total"] == total
            if total:
                assert data["records"][0]["createTime"] == "2026-10-26 08:00:00"
        assert next(iter(remote.files.values()))["created_at"] == "2026-10-26T00:00:00Z"


def test_upload_list_byte_download_delete_and_restart(tmp_path):
    remote = FilesTransport()
    raw = "中文\x00bytes\nexact".encode()
    with TestClient(application(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        kb = create(web)["kbId"]
        result = upload(web, kb, "data:text/plain;base64," + base64.b64encode(raw).decode())
        assert result.status_code == 200, result.text
        validate_contract(result.json(), "KnowledgeFileRecords")
        file = result.json()["data"]["records"][0]
        identifier = file["fileId"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", file["createTime"])
        assert "private" not in result.text and file["status"] == "available"
        assert (
            web.post(
                BASE + "file/list",
                json={"knowledgeBaseId": kb, "fileName": "original", "status": "available"},
            ).json()["data"]["total"]
            == 1
        )
        response = web.get(BASE + "file/download", params={"projectId": "a", "fileId": identifier})
        assert (
            response.status_code == 200
            and hashlib.sha256(response.content).digest() == hashlib.sha256(raw).digest()
        )
        assert "attachment" in response.headers["content-disposition"]
        cookies = dict(web.cookies)
    with TestClient(application(tmp_path, remote)) as web:
        web.cookies.update(cookies)
        assert (
            web.get(BASE + "file/download", params={"projectId": "a", "fileId": identifier}).content
            == raw
        )
        result = web.post(BASE + "file/delete", json={"projectId": "a", "fileIds": [identifier]})
        assert result.status_code == 200 and result.json()["data"]["deleted"] == 1
        assert (
            web.get(
                BASE + "file/download", params={"projectId": "a", "fileId": identifier}
            ).status_code
            == 404
        )
        assert (
            web.post(
                BASE + "file/delete", json={"projectId": "a", "fileIds": [identifier]}
            ).status_code
            == 200
        )


def test_validation_plain_base64_looking_text_and_cross_project(tmp_path):
    remote = FilesTransport()
    with TestClient(application(tmp_path, remote)) as web:
        seed(web.app)
        login(web)
        kb = create(web)["kbId"]
        for name, content in [
            ("../escape.txt", "x"),
            ("C:\\escape.txt", "x"),
            ("x\r\n.txt", "x"),
            ("x.exe", "x"),
            ("x.txt", "data:text/plain;base64,%%%"),
        ]:
            assert upload(web, kb, content, name).status_code == 400
        response = upload(web, kb, "YWJj")
        identifier = response.json()["data"]["records"][0]["fileId"]
        assert (
            web.get(BASE + "file/download", params={"projectId": "a", "fileId": identifier}).content
            == b"YWJj"
        )
        assert (
            web.get(
                BASE + "file/download", params={"projectId": "b", "fileId": identifier}
            ).status_code
            == 404
        )
        assert (
            web.post(
                BASE + "file/delete",
                json={"projectId": "a", "fileIds": [identifier, "unknown-foreign"]},
            ).status_code
            == 404
        )
        assert (
            web.get(
                BASE + "file/download", params={"projectId": "a", "fileId": identifier}
            ).status_code
            == 200
        )
        user = web.app.state.identity.create_account("reader", "reader-password-123")
        web.post("/admin/tenants/a/members", json={"account_id": user["id"], "role": "viewer"})
        login(web, "reader", "reader-password-123")
        assert upload(web, kb, "no").status_code == 403
        assert (
            web.post(
                BASE + "file/delete", json={"projectId": "a", "fileIds": [identifier]}
            ).status_code
            == 403
        )
        assert (
            web.get(
                BASE + "file/download", params={"projectId": "a", "fileId": identifier}
            ).status_code
            == 200
        )
