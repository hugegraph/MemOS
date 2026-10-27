"""Persist explicit operator-configured project mappings; no implicit tenant access."""

import copy
import hashlib
import sqlite3
import time
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .errors import ApiError
from .settings import Settings
from .timefmt import display


class ControlStore:
    actor = None

    def with_actor(self, actor):
        scoped = copy.copy(self)
        scoped.actor = actor
        return scoped

    def role(self, identifier, db=None):
        if db is None:
            with self.connection() as connection:
                return self.role(identifier, connection)
        if self.actor is None:
            return "owner"
        account = db.execute(
            "SELECT platform_admin,disabled FROM accounts WHERE id=?", (self.actor["id"],)
        ).fetchone()
        if account is None or account["disabled"]:
            return None
        if account["platform_admin"]:
            return "owner"
        row = db.execute(
            "SELECT role FROM memberships WHERE tenant_id=? AND account_id=?",
            (identifier, self.actor["id"]),
        ).fetchone()
        return row[0] if row else None

    def require_role(self, identifier, minimum="owner", db=None):
        from .identity_store import forbidden

        if {"viewer": 1, "editor": 2, "owner": 3}.get(self.role(identifier, db), 0) < {
            "viewer": 1,
            "editor": 2,
            "owner": 3,
        }[minimum]:
            raise forbidden()

    def __init__(self, settings: Settings):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = settings.data_dir / "control.db"
        self.allowed = frozenset(pair[0] for pair in settings.projects_seed)
        self.default_project = settings.default_project_id
        with self.connection() as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS projects ("
                "id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                "upstream_cube_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)"
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(projects)")}
            for name, definition in {
                "description": "TEXT NOT NULL DEFAULT ''",
                "is_multi_view": "INTEGER NOT NULL DEFAULT 0",
                "disabled": "INTEGER NOT NULL DEFAULT 0",
                "archived": "INTEGER NOT NULL DEFAULT 0",
                "archived_at": "TEXT",
                "managed": "INTEGER NOT NULL DEFAULT 0",
                "name_key": "TEXT",
            }.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE projects ADD COLUMN {name} {definition}")
            for row in connection.execute(
                "SELECT id,name FROM projects WHERE name_key IS NULL"
            ).fetchall():
                connection.execute(
                    "UPDATE projects SET name_key=? WHERE id=?", (self.name_key(row[1]), row[0])
                )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS project_name_unique ON projects(name_key)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS project_contexts (token_hash TEXT PRIMARY KEY,"
                "selected_project TEXT REFERENCES projects(id),expires_at INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS api_keys ("
                "id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), "
                "name TEXT NOT NULL, token TEXT NOT NULL UNIQUE, "
                "token_hash TEXT NOT NULL UNIQUE, status TEXT NOT NULL, "
                "create_time TEXT NOT NULL, created_by TEXT, revoke_time TEXT, revoked_by TEXT)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS api_keys_active "
                "ON api_keys(project_id, status, create_time DESC, id DESC)"
            )
            for project, cube in settings.projects_seed:
                existing = connection.execute(
                    "SELECT upstream_cube_id FROM projects WHERE id=?", (project,)
                ).fetchone()
                if existing and existing[0] != cube:
                    raise ValueError("Configured project mapping conflicts with stored mapping")
                connection.execute(
                    "INSERT INTO projects (id,name,upstream_cube_id,created_at,name_key) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                    (
                        project,
                        project,
                        cube,
                        datetime.now(timezone.utc).isoformat(),
                        self.name_key(project),
                    ),
                )

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def resolve_cube(self, project_id: str) -> str | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT upstream_cube_id FROM projects "
                "WHERE id=? AND disabled=0 AND archived=0 AND " + self.visibility()[0],
                (project_id, *self.visibility()[1]),
            ).fetchone()
        return row[0] if row else None

    def dictionary(self, default_project: str | None = None, *, context=None) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM projects WHERE disabled=0 AND archived=0 AND "
                + self.visibility()[0]
                + " ORDER BY id",
                self.visibility()[1],
            ).fetchall()
            selected = (
                self.current(context, connection)
                if context is not None or default_project is None
                else default_project
            )
        return [self.project(row, selected) for row in rows]

    def authorized_cubes(self) -> list[str]:
        """Only this process's allowlist, never every persisted project."""
        with self.connection() as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT upstream_cube_id FROM projects WHERE disabled=0 AND archived=0 AND "
                    + self.visibility()[0],
                    self.visibility()[1],
                )
            ]

    @staticmethod
    def name_key(value):
        return unicodedata.normalize("NFKC", value.strip()).casefold()

    def visibility(self):
        ids = sorted(self.allowed)
        where, values = (
            ("(managed=1 OR id IN (" + ",".join("?" for _ in ids) + "))", ids)
            if ids
            else ("managed=1", [])
        )
        if self.actor is not None and not self.actor["platform_admin"]:
            where += " AND id IN (SELECT tenant_id FROM memberships WHERE account_id=?)"
            values.append(self.actor["id"])
        return where, values

    def _row(self, db, identifier):
        row = db.execute(
            "SELECT * FROM projects WHERE id=? AND " + self.visibility()[0],
            (identifier, *self.visibility()[1]),
        ).fetchone()
        if row is None:
            raise ApiError(40001, "项目不存在或未配置访问范围", status_code=404)
        return row

    def current(self, context=None, db=None):
        if db is None:
            with self.connection() as connection:
                return self.current(context, connection)
        selected = None
        if context:
            row = db.execute(
                "SELECT selected_project FROM project_contexts WHERE token_hash=? AND expires_at>?",
                (context, int(time.time())),
            ).fetchone()
            selected = row[0] if row else None
        active = [
            r[0]
            for r in db.execute(
                "SELECT id FROM projects WHERE disabled=0 AND archived=0 AND "
                + self.visibility()[0]
                + " ORDER BY id",
                self.visibility()[1],
            )
        ]
        return (
            selected
            if selected in active
            else self.default_project
            if self.default_project in active
            else active[0]
            if active
            else None
        )

    def ensure_context(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM project_contexts WHERE expires_at<=?", (int(time.time()),))
            db.execute(
                "INSERT OR IGNORE INTO project_contexts VALUES (?,?,?)",
                (digest, self.current(db=db), int(time.time()) + 30 * 86400),
            )
        return digest

    def project(self, row, selected, db=None):
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "createTime": display(row["created_at"]),
            "isMultiView": row["is_multi_view"],
            "status": "ENABLED"
            if row["id"] == selected and not row["disabled"] and not row["archived"]
            else "DISABLED",
            "disabled": bool(row["disabled"]),
            "archived": bool(row["archived"]),
            "kind": row["kind"] if "kind" in row.keys() else "team",
            "role": self.role(row["id"], db),
            **({"archivedAt": display(row["archived_at"])} if row["archived_at"] else {}),
        }

    def page(self, query, context):
        where, values = self.visibility()
        with self.connection() as db:
            db.execute("BEGIN")
            total = db.execute("SELECT count(*) FROM projects WHERE " + where, values).fetchone()[0]
            rows = db.execute(
                "SELECT * FROM projects WHERE "
                + where
                + " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (*values, query.pageSize, (query.pageNum - 1) * query.pageSize),
            ).fetchall()
            selected = self.current(context, db)
        return {"records": [self.project(row, selected) for row in rows], "total": total}

    def create(self, body, context):
        identifier = "proj_" + uuid.uuid4().hex
        try:
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO projects "
                    "(id,name,upstream_cube_id,created_at,name_key,"
                    "description,is_multi_view,managed) "
                    "VALUES (?,?,?,?,?,?,?,1)",
                    (
                        identifier,
                        body.name,
                        identifier,
                        datetime.now(timezone.utc).isoformat(),
                        self.name_key(body.name),
                        body.description,
                        body.isMultiView,
                    ),
                )
                if self.actor is not None:
                    db.execute(
                        "INSERT INTO memberships VALUES (?,?,'owner',?)",
                        (identifier, self.actor["id"], datetime.now(timezone.utc).isoformat()),
                    )
                return self.project(self._row(db, identifier), self.current(context, db), db)
        except sqlite3.IntegrityError as exc:
            raise ApiError(40900, "项目名称已存在", status_code=409) from exc

    def update(self, body, context):
        try:
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                row = self._row(db, body.id)
                if (
                    body.model_fields_set - {"id", "status"}
                    or body.status == "DISABLED"
                    or row["disabled"]
                ):
                    self.require_role(body.id, db=db)
                if row["archived"]:
                    raise ApiError(40900, "请先恢复已归档项目", status_code=409)
                for public, column in (
                    ("name", "name"),
                    ("description", "description"),
                    ("isMultiView", "is_multi_view"),
                ):
                    if public in body.model_fields_set:
                        db.execute(
                            f"UPDATE projects SET {column}=? WHERE id=?",
                            (getattr(body, public), body.id),
                        )
                        if public == "name":
                            db.execute(
                                "UPDATE projects SET name_key=? WHERE id=?",
                                (self.name_key(body.name), body.id),
                            )
                if body.status == "DISABLED":
                    db.execute("UPDATE projects SET disabled=1 WHERE id=?", (body.id,))
                    self._fallback_contexts(db, body.id)
                elif body.status == "ENABLED":
                    if row["disabled"]:
                        db.execute("UPDATE projects SET disabled=0 WHERE id=?", (body.id,))
                    else:
                        db.execute(
                            "UPDATE project_contexts SET selected_project=? WHERE token_hash=?",
                            (body.id, context),
                        )
                return self.project(self._row(db, body.id), self.current(context, db), db)
        except sqlite3.IntegrityError as exc:
            raise ApiError(40900, "项目名称已存在", status_code=409) from exc

    def _fallback_contexts(self, db, identifier):
        db.execute(
            "UPDATE project_contexts SET selected_project=? WHERE selected_project=?",
            (self.current(db=db), identifier),
        )

    def archive(self, identifier, context, *, restore=False):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._row(db, identifier)
            self.require_role(identifier, db=db)
            row = self._row(db, identifier)
            if "kind" in row.keys() and row["kind"] == "personal":
                raise ApiError(40900, "个人空间随账号管理", status_code=409)
            db.execute(
                "UPDATE projects SET archived=?,archived_at=? WHERE id=?",
                (
                    0 if restore else 1,
                    None if restore else datetime.now(timezone.utc).isoformat(),
                    identifier,
                ),
            )
            if not restore:
                self._fallback_contexts(db, identifier)
            return self.project(self._row(db, identifier), self.current(context, db), db)
