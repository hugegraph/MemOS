"""Workspace roles and transactional protection of the last effective owner."""

from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .auth import actor_for, control_for
from .identity_store import conflict, missing, now
from .models import Envelope

Role = Literal["viewer", "editor", "owner"]
router = APIRouter(prefix="/admin/tenants/{tenantId}")


class MemberSet(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    account_id: str | None = Field(None, min_length=1, max_length=128)
    username: str | None = Field(None, min_length=1, max_length=64)
    role: Role

    @model_validator(mode="after")
    def target(self):
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Explicit null fields are not accepted")
        if bool(self.account_id) == bool(self.username):
            raise ValueError("Supply exactly one account_id or username")
        return self


def workspace(store, db, tenant, *, manage=False, active=False):
    row = store._row(db, tenant)
    store.require_role(tenant, "owner" if manage else "viewer", db)
    if row["kind"] != "team":
        raise conflict("个人空间不支持团队成员或邀请操作")
    if active and (row["disabled"] or row["archived"]):
        raise conflict("项目已停用或归档")
    return row


def protect_owner(db, tenant, identifier):
    row = db.execute(
        (
            "SELECT m.role,a.disabled FROM memberships m JOIN "
            "accounts a ON a.id=m.account_id WHERE tenant_id=? AND "
            "account_id=?"
        ),
        (tenant, identifier),
    ).fetchone()
    if row and row["role"] == "owner" and not row["disabled"]:
        count = db.execute(
            (
                "SELECT count(*) FROM memberships m JOIN accounts a ON "
                "a.id=m.account_id WHERE tenant_id=? AND role='owner' "
                "AND a.disabled=0 AND account_id<>?"
            ),
            (tenant, identifier),
        ).fetchone()[0]
        if not count:
            raise conflict("不能移除或降级最后一个有效 owner")


@router.get("/members", response_model=Envelope[dict])
def members(tenantId: str, request: Request, response: Response):
    actor_for(request)
    store = control_for(request)
    with store.connection() as db:
        workspace(store, db, tenantId)
        rows = db.execute(
            (
                "SELECT m.*,a.username,a.display_name,a.disabled FROM "
                "memberships m JOIN accounts a ON a.id=m.account_id "
                "WHERE tenant_id=? ORDER BY m.created_at,m.account_id"
            ),
            (tenantId,),
        ).fetchall()
    response.headers["Cache-Control"] = "no-store"
    records = [
        {
            "account_id": r["account_id"],
            "username": r["username"],
            "display_name": r["display_name"],
            "disabled": bool(r["disabled"]),
            "role": r["role"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return Envelope(data={"records": records, "total": len(records)})


@router.get("/member-candidates", response_model=Envelope[dict])
def candidates(
    tenantId: str, request: Request, response: Response, query: str = Query("", max_length=128)
):
    actor_for(request)
    store = control_for(request)
    with store.connection() as db:
        workspace(store, db, tenantId, manage=True)
        needle = query.strip().removeprefix("@")
        where = "instr(lower(a.username),lower(?))>0 OR instr(lower(a.display_name),lower(?))>0"
        values = (needle, needle)
        total = db.execute("SELECT count(*) FROM accounts a WHERE " + where, values).fetchone()[0]
        rows = db.execute(
            (
                "SELECT a.*,EXISTS(SELECT 1 FROM memberships m WHERE "
                "m.account_id=a.id AND m.tenant_id=?) AS member FROM "
                "accounts a WHERE "
            )
            + where
            + " ORDER BY a.username LIMIT 100",
            (tenantId, *values),
        ).fetchall()
    response.headers["Cache-Control"] = "no-store"
    return Envelope(
        data={
            "records": [
                {
                    "accountId": r["id"],
                    "username": r["username"],
                    "displayName": r["display_name"],
                    "disabled": bool(r["disabled"]),
                    "member": bool(r["member"]),
                }
                for r in rows
            ],
            "total": total,
        }
    )


@router.post("/members", response_model=Envelope[dict])
def set_member(tenantId: str, body: MemberSet, request: Request, response: Response):
    actor_for(request)
    store = control_for(request)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        workspace(store, db, tenantId, manage=True, active=True)
        account = db.execute(
            "SELECT * FROM accounts WHERE " + ("id=?" if body.account_id else "username_key=?"),
            (body.account_id or body.username.casefold(),),
        ).fetchone()
        if account is None:
            raise missing()
        if account["disabled"]:
            raise conflict("不能添加或修改已禁用账号")
        if body.role != "owner":
            protect_owner(db, tenantId, account["id"])
        db.execute(
            (
                "INSERT INTO memberships VALUES (?,?,?,?) ON "
                "CONFLICT(tenant_id,account_id) DO UPDATE SET "
                "role=excluded.role"
            ),
            (tenantId, account["id"], body.role, now()),
        )
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data={"account_id": account["id"], "tenant_id": tenantId, "role": body.role})


def remove(store, tenant, identifier, *, leave=False):
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        workspace(store, db, tenant, manage=not leave)
        if (
            db.execute(
                "SELECT 1 FROM memberships WHERE tenant_id=? AND account_id=?", (tenant, identifier)
            ).fetchone()
            is None
        ):
            raise missing()
        protect_owner(db, tenant, identifier)
        db.execute(
            "DELETE FROM memberships WHERE tenant_id=? AND account_id=?", (tenant, identifier)
        )
        # The next request resolves a per-account fallback; never pick one globally.
        db.execute(
            (
                "UPDATE project_contexts SET selected_project=NULL "
                "WHERE selected_project=? AND token_hash IN (SELECT "
                "token_hash FROM sessions WHERE account_id=?)"
            ),
            (tenant, identifier),
        )
    return {"ok": True}


@router.post("/members/leave", response_model=Envelope[dict])
def leave(tenantId: str, request: Request, response: Response):
    actor = actor_for(request)
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data=remove(control_for(request), tenantId, actor["id"], leave=True))


@router.post("/members/{accountId}/remove", response_model=Envelope[dict])
def remove_member(tenantId: str, accountId: str, request: Request, response: Response):
    actor_for(request)
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data=remove(control_for(request), tenantId, accountId))
