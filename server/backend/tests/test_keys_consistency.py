import json
import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from server.backend.control import ControlStore
from server.backend.tests.test_keys import application, provision

ORIGINAL = ControlStore.connection


@contextmanager
def deny_commit(self):
    with ORIGINAL(self) as db:

        def authorize(action, operation, *_):
            if action == sqlite3.SQLITE_TRANSACTION and operation == "COMMIT":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(authorize)
        yield db


@pytest.mark.parametrize("revoke", [False, True])
def test_commit_failure_restores_committed_manifest(tmp_path, revoke):
    manifest = tmp_path / "gateway/tenants.json"
    with TestClient(application(tmp_path)) as web:
        provision(web)
        key = (
            web.post(
                "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "keep"}
            ).json()["data"]
            if revoke
            else None
        )
        before = manifest.read_bytes()
        with patch.object(ControlStore, "connection", deny_commit):
            result = (
                web.post("/api/dashboard/keys/revoke/" + key["id"], json={})
                if revoke
                else web.post(
                    "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "fail"}
                )
            )
        assert result.status_code == 503
        assert manifest.read_bytes() == before
        with web.app.state.control.connection() as db:
            assert db.execute("SELECT count(*) FROM api_keys WHERE status='active'").fetchone()[
                0
            ] == int(revoke)


def test_compensation_failure_blocks_publish_until_self_heal(tmp_path, monkeypatch):
    from server.backend import key_store

    manifest = tmp_path / "gateway/tenants.json"
    with TestClient(application(tmp_path)) as web:
        provision(web)
        original = key_store.write_tenants_conf
        calls = []

        def fail_repair(path, entries):
            calls.append(1)
            if len(calls) > 1:
                raise OSError("simulated repair failure")
            return original(path, entries)

        monkeypatch.setattr(key_store, "write_tenants_conf", fail_repair)
        # Current code calls the imported writer; target both names for the RED probe.
        monkeypatch.setattr("server.backend.keys.write_tenants_conf", fail_repair, raising=False)
        with patch.object(ControlStore, "connection", deny_commit):
            result = web.post(
                "/api/dashboard/keys/create", json={"projectId": "a", "keyName": "fail"}
            )
        assert result.status_code == 503
        assert manifest.with_name(manifest.name + ".pending").exists()
        monkeypatch.undo()
        key_store.sync_manifest(key_store.ApiKeyStore(web.app.state.control), manifest)
        assert json.loads(manifest.read_text()) == {}
        assert not manifest.with_name(manifest.name + ".pending").exists()


def test_atomic_writer_cleans_secret_temporary_file_on_failure(tmp_path, monkeypatch):
    from server.backend import key_store

    target = tmp_path / "tenants.conf"
    target.write_text("{}")

    def fail(*_):
        raise OSError("replace failure")

    monkeypatch.setattr(key_store.os, "replace", fail)
    with pytest.raises(OSError):
        key_store.write_tenants_conf(target, [("synthetic-value", "a")])
    assert target.read_text() == "{}"
    assert not list(tmp_path.glob("*.tmp"))
