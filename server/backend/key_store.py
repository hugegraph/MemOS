"""接口密钥的仓库与网关清单写入（control.db 的 ``api_keys`` 表）。

key 一旦创建即代表那个项目本身：交付给网关的身份就是 ``user_id=cube_id=<project>``，
网关调用落在项目自己的记忆空间，审计归因与既有租户口径一致（设计 §4/D1）。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
import typing
from contextlib import contextmanager
from pathlib import Path

from .identity_store import now

_TOKEN_RETRIES = 3


class ApiKeyStore:
    """只做库操作；路由负责鉴权与网关配置文件同步（各司其职）。"""

    def __init__(self, control):
        self.control = control

    def _self(self, db, method, *args):
        if db is None:
            with self.control.connection() as connection:
                return method(connection, *args)
        return None

    @staticmethod
    def _token() -> str:
        return "sk-" + secrets.token_hex(24)

    def insert(self, db, project_id: str, name: str, created_by: str | None) -> dict:
        if db is None:
            with self.control.connection() as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                return self.insert(connection, project_id, name, created_by)
        for _ in range(_TOKEN_RETRIES):
            row_id = "key_" + secrets.token_hex(16)
            token = self._token()
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            try:
                db.execute(
                    "INSERT INTO api_keys (id, project_id, name, token, token_hash, "
                    "status, create_time, created_by) VALUES (?,?,?,?,?,'active',?,?)",
                    (row_id, project_id, name, token, token_hash, now(), created_by),
                )
            except sqlite3.IntegrityError as error:
                if "api_keys" in str(error) and "UNIQUE" in str(error):
                    continue
                raise
            row = db.execute("SELECT * FROM api_keys WHERE id=?", (row_id,)).fetchone()
            return dict(row)
        raise RuntimeError("接口密钥 token 生成冲突重试耗尽")

    def get(self, db, key_id: str) -> dict | None:
        if db is None:
            with self.control.connection() as connection:
                return self.get(connection, key_id)
        row = db.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
        return dict(row) if row else None

    def page(self, db, project_id: str, page_num: int, page_size: int) -> tuple[list[dict], int]:
        if db is None:
            with self.control.connection() as connection:
                return self.page(connection, project_id, page_num, page_size)
        total = db.execute(
            "SELECT count(*) FROM api_keys WHERE project_id=? AND status='active'",
            (project_id,),
        ).fetchone()[0]
        rows = db.execute(
            "SELECT * FROM api_keys WHERE project_id=? AND status='active' "
            "ORDER BY create_time DESC, id DESC LIMIT ? OFFSET ?",
            (project_id, page_size, (page_num - 1) * page_size),
        ).fetchall()
        return [dict(row) for row in rows], total

    def active_entries(self, db) -> list[tuple[str, str]]:
        if db is None:
            with self.control.connection() as connection:
                return self.active_entries(connection)
        rows = db.execute(
            "SELECT token, project_id FROM api_keys WHERE status='active' "
            "ORDER BY create_time DESC, id DESC"
        ).fetchall()
        return [(row["token"], row["project_id"]) for row in rows]

    def rename(self, db, key_id: str, name: str) -> dict | None:
        if db is None:
            with self.control.connection() as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                return self.rename(connection, key_id, name)
        db.execute("UPDATE api_keys SET name=? WHERE id=? AND status='active'", (name, key_id))
        row = db.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
        return dict(row) if row else None

    def mark_revoked(self, db, key_id: str, revoked_by: str | None) -> dict | None:
        if db is None:
            with self.control.connection() as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                return self.mark_revoked(connection, key_id, revoked_by)
        cursor = db.execute(
            "UPDATE api_keys SET status='revoked', revoke_time=?, revoked_by=? "
            "WHERE id=? AND status='active'",
            (now(), revoked_by, key_id),
        )
        if cursor.rowcount == 0:
            return None
        row = db.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
        return dict(row)


@contextmanager
def manifest_lock(path: Path):
    """Cross-process publication lock; gateway readers use the same protocol."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def pending_path(path: Path) -> Path:
    return path.with_name(path.name + ".pending")


def write_tenants_conf(path: Path, entries: typing.Sequence[tuple[str, str]]) -> None:
    """Atomic, flushed replacement; callers hold the publication lock."""
    payload = {token: {"user_id": project, "cube_id": project} for token, project in entries}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix="tenants.conf.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(filename, path)
    finally:
        Path(filename).unlink(missing_ok=True)


def _restore_manifest(store, path, writer):
    writer(path, store.active_entries(None))
    pending_path(path).unlink(missing_ok=True)


def mutate_manifest(store, path, change, *, writer=None):
    """Publish only committed state; compensate failures before releasing the lock."""
    from .errors import ApiError

    writer = writer or write_tenants_conf
    marker = pending_path(path)
    with manifest_lock(path):
        if marker.exists():
            try:
                _restore_manifest(store, path, writer)
            except (OSError, ValueError, sqlite3.Error) as error:
                raise ApiError(50300, "网关配置仍待恢复，拒绝继续发布", status_code=503) from error
        touched = False
        try:
            with store.control.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                result = change(db)
                marker.write_text("pending database commit\n", encoding="utf-8")
                touched = True
                writer(path, store.active_entries(db))
                # ControlStore.connection commits here while publication remains locked.
        except Exception as mutation_error:
            if touched:
                try:
                    _restore_manifest(store, path, writer)
                except (OSError, ValueError, sqlite3.Error) as repair_error:
                    cause = (
                        "数据库提交失败"
                        if isinstance(mutation_error, sqlite3.Error)
                        else "网关配置写入失败"
                    )
                    raise ApiError(
                        50300, cause + "，且恢复失败；网关拒绝加载待确认配置", status_code=503
                    ) from repair_error
            raise
        try:
            marker.unlink(missing_ok=True)
        except OSError as error:
            raise ApiError(
                50300, "密钥变更已提交，但配置仍待确认；请恢复后重启网关", status_code=503
            ) from error
        return result


def sync_manifest(store: ApiKeyStore, path: Path) -> None:
    """Startup repair uses the same publication lock and failure marker as writes."""
    with manifest_lock(path):
        pending_path(path).write_text("pending startup reconciliation\n", encoding="utf-8")
        _restore_manifest(store, path, write_tenants_conf)
