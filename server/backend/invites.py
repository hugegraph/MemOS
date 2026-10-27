"""Single-use invite capabilities: only token digests are persisted."""

import re
import secrets
import sqlite3
import time
import uuid
from html import escape

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, field_validator
from starlette.concurrency import run_in_threadpool

from .auth import actor_for, check_csrf, control_for, form, form_fields, page
from .errors import ApiError
from .identity_store import SESSION_COOKIE, conflict, digest, missing, now
from .members import Role, workspace
from .models import Envelope
from .timefmt import display, display_epoch

router = APIRouter()


class InviteRole(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    role: Role


class InviteCreate(InviteRole):
    expiresIn: str = "168h"

    @field_validator("expiresIn")
    @classmethod
    def valid_duration(cls, value):
        if not re.fullmatch(r"[1-9][0-9]{0,6}[smh]", value) or duration(value) > 30 * 86400:
            raise ValueError("Expiry must be positive and no greater than30days")
        return value


def duration(value):
    return int(value[:-1]) * {"s": 1, "m": 60, "h": 3600}[value[-1]]


def status(row):
    return (
        "revoked"
        if row["revoked_at"]
        else "used"
        if row["used_at"]
        else "expired"
        if row["expires_at"] <= int(time.time())
        else "active"
    )


def projection(row):
    return {
        "id": row["id"],
        "tenantId": row["tenant_id"],
        "role": row["role"],
        "status": status(row),
        "createdAt": display(row["created_at"]),
        "expiresAt": display_epoch(row["expires_at"]),
        **({"usedAt": display(row["used_at"])} if row["used_at"] else {}),
        **({"usedByAccountId": row["used_by_account_id"]} if row["used_by_account_id"] else {}),
        **({"revokedAt": display(row["revoked_at"])} if row["revoked_at"] else {}),
    }


class InviteStore:
    def __init__(self, identity):
        self.identity, self.control = identity, identity.control
        with self.control.connection() as db:
            db.execute(
                (
                    "CREATE TABLE IF NOT EXISTS project_invites (id TEXT "
                    "PRIMARY KEY,tenant_id TEXT NOT NULL REFERENCES "
                    "projects(id) ON DELETE CASCADE,token_hash TEXT NOT "
                    "NULL UNIQUE,role TEXT NOT NULL CHECK(role IN "
                    "('viewer','editor','owner')),created_at TEXT NOT "
                    "NULL,expires_at INTEGER NOT NULL,created_by_account_id "
                    "TEXT REFERENCES accounts(id) ON DELETE SET "
                    "NULL,used_at TEXT,used_by_account_id TEXT REFERENCES "
                    "accounts(id) ON DELETE SET NULL,revoked_at TEXT)"
                )
            )

    def valid(self, db, token):
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise missing()
        row = db.execute(
            "SELECT * FROM project_invites WHERE token_hash=?", (digest(token),)
        ).fetchone()
        if row is None:
            raise missing()
        state = status(row)
        if state != "active":
            raise ApiError(
                41000 if state == "expired" else 40900,
                "邀请已过期或不可使用",
                status_code=410 if state == "expired" else 409,
            )
        project = self.control._row(db, row["tenant_id"])
        if project["archived"] or project["disabled"]:
            raise conflict("项目已停用或归档")
        return row, project

    def accept(self, token, actor, username=None, password=None):
        try:
            with self.control.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                invitation, project = self.valid(db, token)
                if actor:
                    account = db.execute(
                        "SELECT * FROM accounts WHERE id=? AND disabled=0", (actor["id"],)
                    ).fetchone()
                    session = db.execute(
                        (
                            "SELECT 1 FROM sessions WHERE token_hash=? "
                            "AND account_id=? AND expires_at>?"
                        ),
                        (actor["session_key"], actor["id"], int(time.time())),
                    ).fetchone()
                    if account is None or session is None:
                        raise ApiError(40100, "请重新登录", status_code=401)
                    identifier = account["id"]
                else:
                    created = self.identity.create_account(username, password, db=db)
                    identifier = created["id"]
                if db.execute(
                    "SELECT 1 FROM memberships WHERE tenant_id=? AND account_id=?",
                    (project["id"], identifier),
                ).fetchone():
                    raise conflict("账号已是该项目成员，邀请未消耗")
                db.execute(
                    "INSERT INTO memberships VALUES (?,?,?,?)",
                    (project["id"], identifier, invitation["role"], now()),
                )
                db.execute(
                    "UPDATE project_invites SET used_at=?,used_by_account_id=? WHERE id=?",
                    (now(), identifier, invitation["id"]),
                )
                session_token = None
                if actor:
                    db.execute(
                        "UPDATE project_contexts SET selected_project=? WHERE token_hash=?",
                        (project["id"], actor["session_key"]),
                    )
                else:
                    session_token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                    expiry = int(time.time()) + self.identity.settings.session_ttl_seconds
                    db.execute(
                        "INSERT INTO sessions VALUES (?,?,?,?)",
                        (digest(session_token), identifier, expiry, csrf),
                    )
                    db.execute(
                        "INSERT INTO project_contexts VALUES (?,?,?)",
                        (digest(session_token), project["id"], expiry),
                    )
                    db.execute(
                        "UPDATE accounts SET last_login_at=? WHERE id=?", (now(), identifier)
                    )
                return session_token
        except sqlite3.IntegrityError as exc:
            raise conflict("用户名已存在或邀请状态冲突") from exc


@router.get("/admin/tenants/{tenantId}/invites", response_model=Envelope[dict])
def list_invites(tenantId: str, request: Request, response: Response):
    actor_for(request)
    store = control_for(request)
    with store.connection() as db:
        workspace(store, db, tenantId, manage=True)
        rows = db.execute(
            "SELECT * FROM project_invites WHERE tenant_id=? ORDER BY created_at DESC,id",
            (tenantId,),
        ).fetchall()
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data={"records": [projection(r) for r in rows], "total": len(rows)})


@router.post("/admin/tenants/{tenantId}/invites", response_model=Envelope[dict])
def create_invite(tenantId: str, body: InviteCreate, request: Request, response: Response):
    actor = actor_for(request)
    store = control_for(request)
    token, identifier, created = secrets.token_urlsafe(32), "inv_" + uuid.uuid4().hex, now()
    expires = int(time.time()) + duration(body.expiresIn)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        workspace(store, db, tenantId, manage=True, active=True)
        db.execute(
            "INSERT INTO project_invites VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL)",
            (identifier, tenantId, digest(token), body.role, created, expires, actor["id"]),
        )
    response.headers["Cache-Control"] = "no-store"
    return Envelope(
        data={
            "id": identifier,
            "tenantId": tenantId,
            "role": body.role,
            "path": "/__login/invite/" + token,
            "createdAt": display(created),
            "expiresAt": display_epoch(expires),
        }
    )


def modify(tenant, identifier, request, role=None):
    actor_for(request)
    store = control_for(request)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        workspace(store, db, tenant, manage=True, active=True)
        row = db.execute(
            "SELECT * FROM project_invites WHERE id=? AND tenant_id=?", (identifier, tenant)
        ).fetchone()
        if row is None:
            raise missing()
        if status(row) != "active":
            if role is None and row["revoked_at"]:
                return {"ok": True}
            raise conflict("只能修改有效邀请")
        if role:
            db.execute("UPDATE project_invites SET role=? WHERE id=?", (role, identifier))
        else:
            db.execute("UPDATE project_invites SET revoked_at=? WHERE id=?", (now(), identifier))
    return {"ok": True, **({"role": role} if role else {})}


@router.post("/admin/tenants/{tenantId}/invites/{inviteId}/revoke", response_model=Envelope[dict])
def revoke(tenantId: str, inviteId: str, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data=modify(tenantId, inviteId, request))


@router.post("/admin/tenants/{tenantId}/invites/{inviteId}/role", response_model=Envelope[dict])
def change_role(
    tenantId: str, inviteId: str, body: InviteRole, request: Request, response: Response
):
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data=modify(tenantId, inviteId, request, body.role))


@router.get("/__login/invite/{token}")
def landing(token: str, request: Request):
    with request.app.state.control.connection() as db:
        invitation, project = request.app.state.invites.valid(db, token)
    actor = request.scope.get("identity")
    csrf = actor["csrf"] if actor else secrets.token_urlsafe(32)
    content = (
        "<p>加入项目："
        + escape(project["name"])
        + "，角色："
        + escape(invitation["role"])
        + '</p><form method="post">'
        + form_fields(csrf)
    )
    if actor:
        content += "<p>当前账号：" + escape(actor["username"]) + "</p><button>接受邀请</button>"
    else:
        content += (
            '<label>新用户名<input name="username" required '
            'maxlength="64" autocomplete="username"></label><label>新'
            '密码<input name="password" type="password" required '
            'minlength="12" maxlength="256" '
            'autocomplete="new-password"></label><button>创建账号并加入</bu'
            "tton>"
        )
    content += "</form>"
    if not actor:
        content += (
            '<p><a href="/__login?next=/__login/invite/'
            + escape(token)
            + '">已有账号？先登录再接受邀请</a></p>'
        )
    response = page("项目邀请", content)
    if not actor:
        response.set_cookie(
            "memos_login_csrf",
            csrf,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            max_age=600,
            path="/__login",
        )
    return response


@router.post("/__login/invite/{token}")
async def accept_invite(token: str, request: Request):
    body = await form(request)
    actor = request.scope.get("identity")
    check_csrf(request, body, actor["csrf"] if actor else None)
    session = await run_in_threadpool(
        request.app.state.invites.accept, token, actor, body.get("username"), body.get("password")
    )
    response = RedirectResponse("/cn/projects", status_code=303)
    if session:
        response.set_cookie(
            SESSION_COOKIE,
            session,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            max_age=request.app.state.settings.session_ttl_seconds,
            path="/",
        )
        response.delete_cookie("memos_login_csrf", path="/__login")
    return response
