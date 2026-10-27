"""Password identities and durable sessions in the existing transactional control DB."""

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone

from .errors import ApiError
from .timefmt import display

SESSION_COOKIE = "memos_session"


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def password_hash(password, salt=None):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ApiError(40000, "密码长度须为12至256字符", status_code=400)
    salt = salt or secrets.token_hex(16)
    value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return salt + ":" + value.hex()


def forbidden():
    return ApiError(40003, "没有执行该操作的权限", status_code=403)


def conflict(message):
    return ApiError(40900, message, status_code=409)


def missing():
    return ApiError(40001, "资源不存在或不可访问", status_code=404)


class IdentityStore:
    def __init__(self, control, settings):
        self.control, self.settings = control, settings
        with control.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            columns = {r[1] for r in db.execute("PRAGMA table_info(projects)")}
            if "kind" not in columns:
                db.execute("ALTER TABLE projects ADD COLUMN kind TEXT NOT NULL DEFAULT 'team'")
                db.execute("ALTER TABLE projects ADD COLUMN owner_account_id TEXT")
            db.execute(
                (
                    "CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY "
                    "KEY, username TEXT NOT NULL UNIQUE, username_key TEXT "
                    "NOT NULL UNIQUE, display_name TEXT NOT NULL, "
                    "password_hash TEXT NOT NULL, platform_admin INTEGER "
                    "NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, "
                    "created_at TEXT NOT NULL, last_login_at TEXT)"
                )
            )
            db.execute(
                (
                    "CREATE TABLE IF NOT EXISTS memberships (tenant_id TEXT "
                    "REFERENCES projects(id) ON DELETE CASCADE, account_id "
                    "TEXT REFERENCES accounts(id) ON DELETE CASCADE, role "
                    "TEXT NOT NULL CHECK(role IN "
                    "('owner','editor','viewer')), created_at TEXT NOT "
                    "NULL, PRIMARY KEY(tenant_id,account_id))"
                )
            )
            db.execute(
                (
                    "CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT "
                    "PRIMARY KEY, account_id TEXT NOT NULL REFERENCES "
                    "accounts(id) ON DELETE CASCADE, expires_at INTEGER NOT "
                    "NULL, csrf TEXT NOT NULL)"
                )
            )
            db.execute(
                (
                    "CREATE TABLE IF NOT EXISTS login_attempts (key TEXT "
                    "PRIMARY KEY, failures INTEGER NOT NULL, blocked_until "
                    "INTEGER NOT NULL)"
                )
            )
        self.dummy_hash = password_hash("dummy-password-not-a-login")

    @staticmethod
    def account(row):
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "platform_admin": bool(row["platform_admin"]),
            "disabled": bool(row["disabled"]),
            "created_at": display(row["created_at"]),
            **({"last_login_at": display(row["last_login_at"])} if row["last_login_at"] else {}),
        }

    def create_account(self, username, password, *, admin=False, bootstrap=False, db=None):
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username):
            raise ApiError(40000, "用户名仅允许1至64位字母数字及._-", status_code=400)
        encoded = password_hash(password)
        if db is None:
            try:
                with self.control.connection() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    return self._create(connection, username, encoded, admin, bootstrap)
            except sqlite3.IntegrityError as exc:
                raise conflict("用户名已存在") from exc
        return self._create(db, username, encoded, admin, bootstrap)

    def _create(self, db, username, encoded, admin, bootstrap):
        if bootstrap and db.execute("SELECT 1 FROM accounts LIMIT 1").fetchone():
            raise conflict("管理员初始化只允许执行一次")
        identifier, created = "acct_" + uuid.uuid4().hex, now()
        db.execute(
            "INSERT INTO accounts VALUES (?,?,?,?,?,?,0,?,NULL)",
            (identifier, username, username.casefold(), username, encoded, int(admin), created),
        )
        # Personal control workspace; deleting an account never deletes upstream memory.
        personal = "personal_" + identifier
        name = "personal-" + identifier
        db.execute(
            (
                "INSERT INTO projects (id,name,upstream_cube_id,created_"
                "at,name_key,managed,kind,owner_account_id) VALUES "
                "(?,?,?,?,?,1,'personal',?)"
            ),
            (personal, name, personal, created, name, identifier),
        )
        db.execute(
            "INSERT INTO memberships VALUES (?,?,'owner',?)", (personal, identifier, created)
        )
        if bootstrap:
            db.execute(
                (
                    "INSERT OR IGNORE INTO memberships SELECT "
                    "id,?,'owner',? FROM projects WHERE kind='team'"
                ),
                (identifier, created),
            )
        return self.account(
            db.execute("SELECT * FROM accounts WHERE id=?", (identifier,)).fetchone()
        )

    def login(self, username, password, address):
        if (
            not isinstance(username, str)
            or len(username) > 64
            or not isinstance(password, str)
            or len(password) > 256
        ):
            raise ApiError(40100, "用户名或密码错误", status_code=401)
        key, timestamp = digest(address), int(time.time())
        with self.control.connection() as db:
            limit = db.execute("SELECT * FROM login_attempts WHERE key=?", (key,)).fetchone()
            if limit and limit["blocked_until"] > timestamp:
                raise ApiError(42900, "登录失败过多，请稍后重试", status_code=429)
            row = db.execute(
                "SELECT * FROM accounts WHERE username_key=?", (username.casefold(),)
            ).fetchone()
        encoded = row["password_hash"] if row else self.dummy_hash
        salt, expected = encoded.split(":")
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1
        ).hex()
        valid = hmac.compare_digest(expected, actual) and row is not None and not row["disabled"]
        with self.control.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not valid:
                db.execute(
                    (
                        "INSERT INTO login_attempts VALUES (?,1,0) ON "
                        "CONFLICT(key) DO UPDATE SET failures=failures+1"
                    ),
                    (key,),
                )
                db.execute(
                    "UPDATE login_attempts SET blocked_until=? WHERE key=? AND failures>=10",
                    (timestamp + 300, key),
                )
            else:
                current = db.execute(
                    "SELECT * FROM accounts WHERE id=? AND disabled=0", (row["id"],)
                ).fetchone()
                if current is None:
                    raise ApiError(40100, "用户名或密码错误", status_code=401)
                db.execute("DELETE FROM login_attempts WHERE key=?", (key,))
                db.execute(
                    "DELETE FROM project_contexts WHERE token_hash IN "
                    "(SELECT token_hash FROM sessions WHERE expires_at<=?)",
                    (timestamp,),
                )
                db.execute("DELETE FROM sessions WHERE expires_at<=?", (timestamp,))
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                expiry = timestamp + self.settings.session_ttl_seconds
                db.execute(
                    "INSERT INTO sessions VALUES (?,?,?,?)",
                    (digest(token), row["id"], expiry, csrf),
                )
                scoped = self.control.with_actor(self.account(current))
                db.execute(
                    "INSERT INTO project_contexts VALUES (?,?,?)",
                    (digest(token), scoped.current(db=db), expiry),
                )
                db.execute("UPDATE accounts SET last_login_at=? WHERE id=?", (now(), row["id"]))
                return token
        raise ApiError(40100, "用户名或密码错误", status_code=401)

    def identity(self, token):
        if not token or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        with self.control.connection() as db:
            row = db.execute(
                (
                    "SELECT a.*,s.token_hash,s.csrf FROM sessions s JOIN "
                    "accounts a ON a.id=s.account_id WHERE s.token_hash=? "
                    "AND s.expires_at>? AND a.disabled=0"
                ),
                (digest(token), int(time.time())),
            ).fetchone()
        return (
            {**self.account(row), "session_key": row["token_hash"], "csrf": row["csrf"]}
            if row
            else None
        )

    def logout(self, key):
        with self.control.connection() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (key,))
            db.execute("DELETE FROM project_contexts WHERE token_hash=?", (key,))

    def account_list(self, query, page, size):
        with self.control.connection() as db:
            db.execute("BEGIN")
            where = "instr(lower(username),lower(?))>0 OR instr(lower(display_name),lower(?))>0"
            values = (query, query)
            total = db.execute("SELECT count(*) FROM accounts WHERE " + where, values).fetchone()[0]
            rows = db.execute(
                "SELECT * FROM accounts WHERE "
                + where
                + " ORDER BY created_at,id LIMIT ? OFFSET ?",
                (*values, size, (page - 1) * size),
            ).fetchall()
            return {"records": [self.account(r) for r in rows], "total": total}

    @staticmethod
    def effective_guards(db, account_id, *, lose_admin=False, lose_owner=False):
        row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if row is None:
            raise missing()
        if lose_admin and row["platform_admin"] and not row["disabled"]:
            if (
                db.execute(
                    "SELECT count(*) FROM accounts WHERE platform_admin=1 AND disabled=0 AND id<>?",
                    (account_id,),
                ).fetchone()[0]
                == 0
            ):
                raise conflict("不能移除最后一个有效平台管理员")
        if lose_owner and not row["disabled"]:
            for owned in db.execute(
                (
                    "SELECT tenant_id FROM memberships JOIN projects ON "
                    "projects.id=tenant_id WHERE account_id=? AND "
                    "role='owner' AND kind='team'"
                ),
                (account_id,),
            ):
                if (
                    db.execute(
                        (
                            "SELECT count(*) FROM memberships m JOIN accounts a ON "
                            "a.id=m.account_id WHERE m.tenant_id=? AND role='owner' "
                            "AND a.disabled=0 AND a.id<>?"
                        ),
                        (owned[0], account_id),
                    ).fetchone()[0]
                    == 0
                ):
                    raise conflict("该账号是项目最后一个有效 owner，请先转移所有权")
        return row

    def update_account(self, identifier, body):
        changes = body.model_dump(exclude_unset=True)
        with self.control.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.effective_guards(
                db,
                identifier,
                lose_admin=changes.get("disabled") is True
                or changes.get("platform_admin") is False,
                lose_owner=changes.get("disabled") is True,
            )
            for column, value in changes.items():
                db.execute(f"UPDATE accounts SET {column}=? WHERE id=?", (value, identifier))
            if changes.get("disabled") is True:
                self.revoke_account_sessions(db, identifier)
            return self.account(
                db.execute("SELECT * FROM accounts WHERE id=?", (identifier,)).fetchone()
            )

    @staticmethod
    def revoke_account_sessions(db, identifier):
        db.execute(
            (
                "DELETE FROM project_contexts WHERE token_hash IN "
                "(SELECT token_hash FROM sessions WHERE account_id=?)"
            ),
            (identifier,),
        )
        db.execute("DELETE FROM sessions WHERE account_id=?", (identifier,))

    def delete_account(self, identifier):
        with self.control.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.effective_guards(db, identifier, lose_admin=True, lose_owner=True)
            self.revoke_account_sessions(db, identifier)
            db.execute(
                (
                    "UPDATE project_contexts SET selected_project=NULL "
                    "WHERE selected_project IN (SELECT id FROM projects "
                    "WHERE owner_account_id=?)"
                ),
                (identifier,),
            )
            db.execute(
                "DELETE FROM projects WHERE kind='personal' AND owner_account_id=?", (identifier,)
            )
            db.execute("DELETE FROM accounts WHERE id=?", (identifier,))
        return {"ok": True}
