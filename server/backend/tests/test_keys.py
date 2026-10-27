"""Task 2 RED：/api/dashboard/keys 五条路由（owner-only、契约形状、文件同步回滚、审计打码）。"""

import json
import re

import httpx
from fastapi.testclient import TestClient
from server.backend.app import create_app
from server.backend.settings import Settings
from server.backend.tests.test_accounts import login, seed

FIVE_ROUTES = (
    ("/api/dashboard/keys/list", {"projectId": "a"}),
    ("/api/dashboard/keys/create", {"projectId": "a", "keyName": "x"}),
    ("/api/dashboard/keys/copy", {"apiKeyId": "key_" + "0" * 32}),
    ("/api/dashboard/keys/rename/x", {"keyName": "x"}),
    ("/api/dashboard/keys/revoke/x", {}),
)

def application(tmp_path, **overrides):
    settings = Settings(
        data_dir=tmp_path / "data",
        projects_seed=(("a", "cube-a"), ("b", "cube-b")),
        default_project_id="a",
        auth_enabled=True,
        gateway_keys_file=overrides.pop("gateway_keys_file", tmp_path / "gateway" / "tenants.json"),
        audit_console_dir=overrides.pop("audit_console_dir", None),
        **overrides,
    )
    return create_app(settings, transport=httpx.MockTransport(lambda r: httpx.Response(500)))


def provision(web):
    admin = seed(web.app)
    member = web.app.state.identity.create_account("member", "member-password-123")
    login(web)
    return admin, member


def grant_admin(web):
    """回到管理员会话（平台管理员对所有项目隐式 owner）。"""
    login(web)


def login_member(web):
    login(web, "member", "member-password-123")


def test_create_contract_shape_and_format(tmp_path):
    with TestClient(application(tmp_path)) as web:
        provision(web)
        body = web.post("/api/dashboard/keys/create", json={"projectId": "a", "keyName": "主密钥"})
        assert body.status_code == 200
        envelope = body.json()
        assert envelope["code"] == 0 and envelope["message"] == "success"
        data = envelope["data"]
        assert re.fullmatch(r"key_[0-9a-f]{32}", data["id"])
        assert data["keyName"] == "主密钥"
        assert re.fullmatch(r"sk-[0-9a-f]{48}", data["apiKey"])
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", data["createTime"])
        second = web.post(
            "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "二"}
        ).json()["data"]
        assert second["apiKey"] != data["apiKey"] and second["id"] != data["id"]


def test_list_contract_with_stable_pagination(tmp_path):
    with TestClient(application(tmp_path)) as web:
        provision(web)
        tokens = {}
        for name in ("k1", "k2", "k3"):
            row = web.post(
                "/api/dashboard/keys/create", json={"projectId": "a", "keyName": name}
            ).json()["data"]
            tokens[name] = row["apiKey"]
        page = web.post(
            "/api/dashboard/keys/list", json={"projectId": "a", "pageNum": 1, "pageSize": 2}
        ).json()["data"]
        assert page["total"] == 3 and len(page["records"]) == 2
        assert {r["apiKey"] for r in page["records"]} <= set(tokens.values())
        tail = web.post(
            "/api/dashboard/keys/list", json={"projectId": "a", "pageNum": 2, "pageSize": 2}
        ).json()["data"]
        assert tail["total"] == 3 and {r["id"] for r in page["records"]}.isdisjoint(
            {r["id"] for r in tail["records"]}
        )


def test_copy_returns_raw_string(tmp_path):
    with TestClient(application(tmp_path)) as web:
        provision(web)
        row = web.post(
            "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "k"}
        ).json()["data"]
        copied = web.post("/api/dashboard/keys/copy", json={"apiKeyId": row["id"]})
        assert copied.status_code == 200
        assert copied.json()["code"] == 0 and copied.json()["data"] == row["apiKey"]


def test_revoke_soft_and_aftermath(tmp_path):
    with TestClient(application(tmp_path)) as web:
        provision(web)
        row = web.post(
            "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "k"}
        ).json()["data"]
        revoked = web.post(f"/api/dashboard/keys/revoke/{row['id']}", json={})
        assert revoked.status_code == 200 and revoked.json()["data"] == {"ok": True}
        listing = web.post("/api/dashboard/keys/list", json={"projectId": "a"}).json()["data"]
        assert listing["total"] == 0
        assert web.post("/api/dashboard/keys/copy", json={"apiKeyId": row["id"]}).status_code == 404
        assert (
            web.post(
                f"/api/dashboard/keys/rename/{row['id']}", json={"keyName": "n"}
            ).status_code
            == 404
        )
        with web.app.state.control.connection() as db:
            kept = db.execute(
                "SELECT status, token FROM api_keys WHERE id=?", (row["id"],)
            ).fetchone()
        assert kept["status"] == "revoked" and kept["token"] == row["apiKey"]


def test_rename_contract_and_manifest_untouched(tmp_path):
    manifest = tmp_path / "gateway" / "tenants.json"
    with TestClient(application(tmp_path)) as web:
        provision(web)
        row = web.post(
            "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "before"}
        ).json()["data"]
        before = manifest.read_bytes()
        renamed = web.post(
            f"/api/dashboard/keys/rename/{row['id']}", json={"keyName": "after"}
        )
        assert renamed.status_code == 200 and renamed.json()["data"]["keyName"] == "after"
        assert manifest.read_bytes() == before


def test_manifest_written_on_create(tmp_path):
    manifest = tmp_path / "gateway" / "tenants.json"
    with TestClient(application(tmp_path)) as web:
        provision(web)
        row = web.post(
            "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "k"}
        ).json()["data"]
        listing = json.loads(manifest.read_text(encoding="utf-8"))
        assert listing == {row["apiKey"]: {"user_id": "a", "cube_id": "a"}}


def test_role_matrix_forbidden_probes(tmp_path):
    with TestClient(application(tmp_path)) as web:
        admin, member = provision(web)

        def member_role(role):
            grant_admin(web)
            put = web.post(
                "/admin/tenants/a/members", json={"account_id": member["id"], "role": role}
            )
            assert put.status_code == 200, put.text
            owned = web.post(
                "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "owned"}
            ).json()["data"]
            login_member(web)
            assert web.post("/api/dashboard/keys/list", json={"projectId": "a"}).status_code == 403
            assert (
                web.post(
                    "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "n"}
                ).status_code
                == 403
            )
            assert (
                web.post("/api/dashboard/keys/copy", json={"apiKeyId": owned["id"]}).status_code
                == 403
            )
            assert (
                web.post(f"/api/dashboard/keys/revoke/{owned['id']}", json={}).status_code == 403
            )
            assert (
                web.post(
                    f"/api/dashboard/keys/rename/{owned['id']}", json={"keyName": "n"}
                ).status_code
                == 403
            )

        member_role("viewer")
        member_role("editor")

        # 平台管理员建在 a 的密钥对 b 会话不可见：先把成员改成 b-only。
        grant_admin(web)
        assert (
            web.post(f"/admin/tenants/a/members/{member['id']}/remove", json={}).status_code == 200
        )
        grant_admin(web)
        assert (
            web.post("/admin/tenants/b/members", json={"account_id": member["id"], "role": "owner"})
            .status_code
            == 200
        )
        web.post(
            "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "hidden"}
        ).json()["data"]
        login_member(web)
        assert web.post("/api/dashboard/keys/list", json={"projectId": "a"}).status_code == 404
        own = web.post("/api/dashboard/keys/list", json={"projectId": "b"}).json()["data"]
        assert own["total"] == 0

        # 匿名（无 cookie 的独立 client）一律 401。
        with TestClient(application(tmp_path)) as stranger:
            for route, body in FIVE_ROUTES:
                assert stranger.post(route, json=body).status_code == 401


def test_auth_off_mode_owner_pass(tmp_path):
    off = Settings(
        data_dir=tmp_path / "off" / "data",
        projects_seed=(("a", "cube-a"), ("b", "cube-b")),
        default_project_id="a",
        auth_enabled=False,
        gateway_keys_file=tmp_path / "off" / "tenants.json",
    )
    with TestClient(
        create_app(off, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    ) as web:
        created = web.post(
            "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "ops"}
        ).json()["data"]
        assert re.fullmatch(r"sk-[0-9a-f]{48}", created["apiKey"])
        listing = web.post("/api/dashboard/keys/list", json={"projectId": "a"}).json()["data"]
        assert listing["total"] == 1


def test_create_not_configured_503(tmp_path):
    with TestClient(application(tmp_path, gateway_keys_file=None)) as web:
        provision(web)
        failed_create = web.post(
            "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "x"}
        )
        assert failed_create.status_code == 503
        assert "交付路径" in failed_create.json()["message"]
        failed_revoke = web.post("/api/dashboard/keys/revoke/x", json={})
        assert failed_revoke.status_code == 503
        assert "交付路径" in failed_revoke.json()["message"]
        for route, payload in (
            ("/api/dashboard/keys/copy", {"apiKeyId": "key_" + "0" * 32}),
            ("/api/dashboard/keys/rename/x", {"keyName": "x"}),
        ):
            assert web.post(route, json=payload).status_code == 404, route
        assert web.post("/api/dashboard/keys/list", json={"projectId": "a"}).status_code == 200


def test_create_rolls_back_if_manifest_write_fails(tmp_path, monkeypatch):
    manifest = tmp_path / "gateway" / "tenants.json"
    with TestClient(application(tmp_path)) as web:
        provision(web)

        def boom(path, entries):
            raise OSError("disk on fire")

        monkeypatch.setattr("server.backend.keys.write_tenants_conf", boom)
        failed = web.post("/api/dashboard/keys/create", json={"projectId": "a", "keyName": "k"})
        assert failed.status_code == 503
        assert "网关配置写入失败" in failed.json()["message"]
        monkeypatch.undo()
        libs = web.post("/api/dashboard/keys/list", json={"projectId": "a"}).json()["data"]
        assert libs["total"] == 0
        with web.app.state.control.connection() as db:
            count = db.execute("SELECT count(*) FROM api_keys").fetchone()[0]
        # 启动自愈已写出空清单（Task 3 起 manifest 恒存在），回滚后的重点：零 token。
        assert count == 0 and json.loads(manifest.read_text(encoding="utf-8")) == {}


def test_revoke_rolls_back_if_manifest_write_fails(tmp_path, monkeypatch):
    manifest = tmp_path / "gateway" / "tenants.json"
    with TestClient(application(tmp_path)) as web:
        provision(web)
        row = web.post(
            "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "k"}
        ).json()["data"]
        before = manifest.read_bytes()

        def boom(path, entries):
            raise OSError("disk on fire")

        monkeypatch.setattr("server.backend.keys.write_tenants_conf", boom)
        failed = web.post(f"/api/dashboard/keys/revoke/{row['id']}", json={})
        assert failed.status_code == 503
        monkeypatch.undo()
        assert web.post("/api/dashboard/keys/copy", json={"apiKeyId": row["id"]}).json()[
            "data"
        ] == row["apiKey"]
        assert manifest.read_bytes() == before


def test_self_heal_on_startup(tmp_path):
    gateway = tmp_path / "gateway" / "tenants.json"
    with TestClient(application(tmp_path)) as web:
        provision(web)
        row = web.post(
            "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "k"}
        ).json()["data"]
    # 模拟一次崩溃的 create：manifest 里留下一把库里没有的孤儿 token。
    orphan = "sk-" + "0" * 48
    gateway.write_text(
        json.dumps(
            {
                orphan: {"user_id": "a", "cube_id": "a"},
                row["apiKey"]: {"user_id": "a", "cube_id": "a"},
            }
        ),
        encoding="utf-8",
    )
    with TestClient(application(tmp_path)) as web:
        pass  # TestClient 进入即触发 lifespan：启动自愈就在此发生。
    payload = json.loads(gateway.read_text(encoding="utf-8"))
    assert set(payload) == {row["apiKey"]} and orphan not in payload

    # 空库启动 → 写出合法空清单而不是干脆不写。
    empty = tmp_path / "empty"
    with TestClient(application(empty, gateway_keys_file=empty / "tenants.json")) as web:
        pass
    assert json.loads((empty / "tenants.json").read_text(encoding="utf-8")) == {}


def test_archived_project_keys_hidden(tmp_path):
    with TestClient(application(tmp_path)) as web:
        provision(web)
        created = web.post(
            "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "k"}
        ).json()["data"]
        assert (
            web.post("/api/dashboard/project/revoke/a", json={}).status_code == 200
        )  # 既有语义：legacy revoke = 可恢复归档
        probes = (
            ("/api/dashboard/keys/list", {"projectId": "a"}),
            ("/api/dashboard/keys/copy", {"apiKeyId": created["id"]}),
            (f"/api/dashboard/keys/rename/{created['id']}", {"keyName": "n"}),
        )
        for route, body in probes:
            probe = web.post(route, json=body)
            assert probe.status_code == 404, route
        assert web.post(f"/api/dashboard/keys/revoke/{created['id']}", json={}).status_code == 404
        assert (
            web.post("/api/dashboard/project/restore/a", json={}).status_code == 200
        )
        restored = web.post("/api/dashboard/keys/list", json={"projectId": "a"}).json()["data"]
        assert restored["total"] == 1 and restored["records"][0]["apiKey"].startswith("sk-")


def test_console_audit_redacts_token(tmp_path):
    console = tmp_path / "console"
    with TestClient(application(tmp_path, audit_console_dir=str(console))) as web:
        provision(web)
        row = web.post(
            "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "k"}
        ).json()["data"]
    from server.backend.audit_normalize import redact

    texts = []
    for path in console.rglob("*"):
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8", errors="ignore"))
    combined = "\n".join(texts)
    # ①恒久不变量：console 目录里不允许出现完整 sk- 值（keys 路由不在有界捕获集内）。
    assert row["apiKey"] not in combined
    assert "/api/dashboard/keys/create" not in combined
    # ②管底打码回归钉：audit_normalize 对与密钥同形载荷永远打码。
    assert redact({"apiKey": row["apiKey"]})["apiKey"] == "[REDACTED]"
    assert "[REDACTED]" in redact(f"raw {row['apiKey']} tail")
