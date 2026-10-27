"""Task 1 RED：control.db 的 api_keys 表 + key_store 的最小仓库语义。"""

import hashlib
import json

from server.backend.control import ControlStore
from server.backend.key_store import ApiKeyStore, sync_manifest, write_tenants_conf
from server.backend.settings import Settings


def store_on(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        projects_seed=(("a", "cube-a"), ("b", "cube-b")),
        default_project_id="a",
        auth_enabled=True,
    )
    control = ControlStore(settings)
    return control, ApiKeyStore(control)


def insert(control, store, project_id, name):
    with control.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        return store.insert(db, project_id, name, None)


def test_create_format_and_listing(tmp_path):
    control, store = store_on(tmp_path)
    rows = [
        insert(control, store, "a", "k1"),
        insert(control, store, "a", "k2"),
        insert(control, store, "a", "k3"),
    ]
    row = rows[0]
    assert row["id"].startswith("key_") and len(row["id"]) == 36
    int(row["id"][4:], 16)
    assert row["token"].startswith("sk-") and len(row["token"]) == 51
    int(row["token"][3:], 16)
    assert row["token_hash"] == hashlib.sha256(row["token"].encode()).hexdigest()
    assert row["status"] == "active" and row["revoke_time"] is None and row["revoked_by"] is None
    records, total = store.page(None, "a", 1, 10)
    expected = sorted(rows, key=lambda r: (r["create_time"], r["id"]), reverse=True)
    assert total == 3 and [r["id"] for r in records] == [r["id"] for r in expected]
    tail, tail_total = store.page(None, "a", 2, 2)
    assert tail_total == 3 and [r["id"] for r in tail] == [r["id"] for r in expected[2:]]


def test_rename_keeps_token_and_status(tmp_path):
    control, store = store_on(tmp_path)
    row = insert(control, store, "a", "old")
    with control.connection() as db:
        renamed = store.rename(db, row["id"], "new")
    assert renamed["name"] == "new"
    assert renamed["token"] == row["token"] and renamed["status"] == "active"
    assert renamed["create_time"] == row["create_time"]
    assert store.rename(None, "key_" + "0" * 32, "x") is None


def test_revoke_soft_row_remains(tmp_path):
    control, store = store_on(tmp_path)
    row = insert(control, store, "a", "k")
    with control.connection() as db:
        revoked = store.mark_revoked(db, row["id"], "admin-1")
    assert revoked["status"] == "revoked"
    assert revoked["revoke_time"] and revoked["revoked_by"] == "admin-1"
    assert store.active_entries(None) == []
    assert store.get(None, row["id"])["token"] == row["token"]
    with control.connection() as db:
        assert store.mark_revoked(db, row["id"], None) is None


def test_persistence_across_control_instances(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        projects_seed=(("a", "cube-a"), ("b", "cube-b")),
        default_project_id="a",
        auth_enabled=True,
    )
    control = ControlStore(settings)
    store = ApiKeyStore(control)
    first = insert(control, store, "a", "live")
    dead = insert(control, store, "a", "dead")
    with control.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        store.mark_revoked(db, dead["id"], None)
    fresh = ApiKeyStore(ControlStore(Settings(
        data_dir=tmp_path / "data",
        projects_seed=(("a", "cube-a"), ("b", "cube-b")),
        default_project_id="a",
        auth_enabled=True,
    )))
    records, total = fresh.page(None, "a", 1, 10)
    assert total == 1 and records[0]["id"] == first["id"]
    assert fresh.get(None, first["id"])["status"] == "active"
    assert fresh.active_entries(None) == [(first["token"], "a")]


def test_write_tenants_conf_shape_and_atomic(tmp_path):
    target = tmp_path / "tenants.conf"
    write_tenants_conf(target, [("tok1", "a"), ("tok2", "b")])
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "tok1": {"user_id": "a", "cube_id": "a"},
        "tok2": {"user_id": "b", "cube_id": "b"},
    }
    assert target.read_text(encoding="utf-8").endswith("\n")
    assert [entry.name for entry in tmp_path.iterdir()] == ["tenants.conf"]


def test_sync_manifest_from_store(tmp_path):
    control, store = store_on(tmp_path)
    live = insert(control, store, "a", "live")
    other = insert(control, store, "b", "other")
    manifest = tmp_path / "m.json"
    sync_manifest(store, manifest)
    loaded = json.loads(manifest.read_text(encoding="utf-8"))
    assert set(loaded) == {live["token"], other["token"]}
    with control.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        store.mark_revoked(db, live["id"], None)
    sync_manifest(store, manifest)
    loaded = json.loads(manifest.read_text(encoding="utf-8"))
    assert set(loaded) == {other["token"]}
    assert loaded == {other["token"]: {"user_id": "b", "cube_id": "b"}}
