"""Session enforcement and backend-owned HTML forms, without React changes."""

import hmac
import re
import secrets
import sqlite3
from html import escape
from urllib.parse import parse_qs

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool

from .auth_ui import STYLE_SOURCE, render
from .errors import ApiError
from .identity_store import SESSION_COOKIE, forbidden
from .models import Envelope
from .security import browser_write_receive, check_origin

router = APIRouter()


def control_for(request):
    actor = request.scope.get("identity")
    store = request.app.state.control
    return store.with_actor(actor) if actor else store


def actor_for(request, *, admin=False):
    actor = request.scope.get("identity")
    if actor is None:
        raise ApiError(40100, "请先登录", status_code=401)
    if admin and not actor["platform_admin"]:
        raise forbidden()
    return actor


class SessionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["app"].state.settings.auth_enabled:
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        try:
            actor = await run_in_threadpool(
                scope["app"].state.identity.identity, request.cookies.get(SESSION_COOKIE)
            )
            scope["identity"] = actor
            protected = (
                scope["path"].startswith(("/api/dashboard/", "/admin/"))
                or scope["path"] == "/api/auth/me"
            )
            if protected and actor is None:
                raise ApiError(40100, "请先登录", status_code=401)
            if protected and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
                check_origin(request)
                if request.headers.get("origin") is not None:
                    receive = await browser_write_receive(request, receive)
        except ApiError as exc:
            response = JSONResponse(
                {"code": exc.code, "data": None, "message": exc.message},
                status_code=exc.status_code,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        except sqlite3.Error:
            await JSONResponse(
                {"code": 50300, "data": None, "message": "身份存储暂不可用"}, status_code=503
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)


def page(title, content, status=200):
    return HTMLResponse(
        render(title, content),
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "same-origin",
            "Content-Security-Policy": (
                "default-src 'none'; form-action 'self'; frame-ancestors 'none'; "
                "style-src " + STYLE_SOURCE
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


def form_fields(token):
    return '<input type="hidden" name="csrf" value="' + escape(token) + '">'


async def form(request):
    check_origin(request)
    if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise ApiError(40000, "表单格式错误", status_code=400)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8192:
            raise ApiError(40000, "表单过大", status_code=400)
    try:
        values = parse_qs(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=10)
        if any(len(v) != 1 for v in values.values()):
            raise ValueError()
        return {k: v[0] for k, v in values.items()}
    except (ValueError, UnicodeError) as exc:
        raise ApiError(40000, "表单格式错误", status_code=400) from exc


def check_csrf(request, body, expected=None):
    expected = expected or request.cookies.get("memos_login_csrf", "")
    supplied = body.get("csrf", "")
    if (
        not re.fullmatch(r"[A-Za-z0-9_-]{43}", supplied)
        or not expected
        or len(expected) != 43
        or not hmac.compare_digest(supplied, expected)
    ):
        raise forbidden()


@router.get("/__login", response_class=HTMLResponse)
def login_page(request: Request):
    token = secrets.token_urlsafe(32)
    response = page(
        "登录 MemOS",
        '<form method="post">'
        + form_fields(token)
        + (
            '<label>用户名<input name="username" required '
            'maxlength="64" autocomplete="username" placeholder="请输入用户名"></label><label>密'
            '码<input name="password" type="password" required '
            'maxlength="256" autocomplete="current-password" placeholder="请输入密码"></label'
            "><button>登录</button></form>"
        )
        + '<a class="secondary" href="/__login/register">还没有账号？注册账号</a>'
        + (
            '<p class="note" role="status">注册成功，请登录。</p>'
            if request.query_params.get("registered") == "1"
            else ""
        ),
    )
    response.set_cookie(
        "memos_login_csrf",
        token,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=600,
        path="/__login",
    )
    next_path = request.query_params.get("next", "")
    if re.fullmatch(r"/__login/invite/[A-Za-z0-9_-]{43}", next_path):
        # The sole permitted continuation is a same-origin invitation page.
        response.body = response.body.replace(
            b"</form>",
            ('<input type="hidden" name="next" value="' + escape(next_path) + '"></form>').encode(),
        )
        response.headers["content-length"] = str(len(response.body))
    return response


def registration_form(token, *, error=None, username=""):
    content = (
        ('<p class="note" role="alert">' + escape(error) + "</p>" if error else "")
        + '<form method="post">'
        + form_fields(token)
        + '<label>账号<input name="username" required maxlength="64" '
        'pattern="[A-Za-z0-9_.-]{1,64}" autocomplete="username" '
        'placeholder="字母、数字及 . _ -" value="' + escape(username, quote=True) + '"></label>'
        '<label>密码<input name="password" type="password" required minlength="12" '
        'maxlength="256" autocomplete="new-password" placeholder="至少12个字符"></label>'
        '<label>确认密码<input name="confirm_password" type="password" required '
        'minlength="12" maxlength="256" autocomplete="new-password" '
        'placeholder="请再次输入密码"></label><button>注册账号</button></form>'
        '<a class="secondary" href="/__login">已有账号？返回登录</a>'
    )
    return page("注册 MemOS", content)


@router.get("/__login/register", response_class=HTMLResponse)
def register_page(request: Request):
    token = secrets.token_urlsafe(32)
    response = registration_form(token)
    response.set_cookie(
        "memos_login_csrf",
        token,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=600,
        path="/__login",
    )
    return response


@router.post("/__login/register")
async def register_submit(request: Request):
    body = await form(request)
    check_csrf(request, body)
    try:
        if set(body) - {"csrf", "username", "password", "confirm_password"}:
            raise ApiError(40000, "注册信息包含不支持的字段", status_code=400)
        if body.get("password") != body.get("confirm_password"):
            raise ApiError(40000, "两次输入的密码不一致", status_code=400)
        await run_in_threadpool(
            request.app.state.identity.create_account,
            body.get("username", ""),
            body.get("password", ""),
        )
    except ApiError as exc:
        response = registration_form(
            body["csrf"], error=exc.message, username=body.get("username", "")[:64]
        )
        response.status_code = exc.status_code
        return response
    response = RedirectResponse("/__login?registered=1", status_code=303)
    response.delete_cookie("memos_login_csrf", path="/__login")
    return response


@router.post("/__login")
async def login_submit(request: Request):
    body = await form(request)
    check_csrf(request, body)
    token = await run_in_threadpool(
        request.app.state.identity.login,
        body.get("username", ""),
        body.get("password", ""),
        request.client.host if request.client else "local",
    )
    next_path = body.get("next", "")
    response = RedirectResponse(
        next_path
        if re.fullmatch(r"/__login/invite/[A-Za-z0-9_-]{43}", next_path)
        else "/cn/projects",
        status_code=303,
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=request.app.state.settings.session_ttl_seconds,
        path="/",
    )
    response.delete_cookie("memos_login_csrf", path="/__login")
    return response


@router.get("/__logout", response_class=HTMLResponse)
def logout_page(request: Request):
    actor = actor_for(request)
    return page(
        "退出登录",
        '<p class="note">退出后，你需要重新登录才能访问控制台。</p>'
        '<form method="post">'
        + form_fields(actor["csrf"])
        + '<button>确认退出</button></form><a class="secondary" href="/cn/projects">返回控制台</a>',
    )


@router.post("/__logout")
async def logout_submit(request: Request):
    actor = actor_for(request)
    check_csrf(request, await form(request), actor["csrf"])
    await run_in_threadpool(request.app.state.identity.logout, actor["session_key"])
    response = RedirectResponse("/__login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/api/auth/me", response_model=Envelope[dict])
def me(request: Request, response: Response):
    actor = actor_for(request)
    store = control_for(request)
    projects = store.dictionary(context=actor["session_key"])
    response.headers["Cache-Control"] = "no-store"
    return Envelope(
        data={
            "accountId": actor["id"],
            "username": actor["username"],
            "displayName": actor["display_name"],
            "platformAdmin": actor["platform_admin"],
            "currentProjectId": store.current(actor["session_key"]) or "",
            "projects": [{k: p[k] for k in ("id", "name", "role", "kind")} for p in projects],
        }
    )


class AccountUpdate(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    display_name: str | None = Field(None, min_length=1, max_length=128)
    disabled: bool | None = None
    platform_admin: bool | None = None

    @model_validator(mode="after")
    def changes(self):
        if not self.model_fields_set or any(
            getattr(self, k) is None for k in self.model_fields_set
        ):
            raise ValueError("Expected explicit non-null fields")
        if self.display_name is not None and (
            not self.display_name.strip() or any(ord(c) < 32 for c in self.display_name)
        ):
            raise ValueError("Invalid name")
        return self


@router.get("/admin/accounts", response_model=Envelope[dict])
def accounts(
    request: Request,
    response: Response,
    query: str = Query("", max_length=128),
    page: int = Query(1, ge=1, le=2147483647),
    pageSize: int = Query(20, ge=1, le=200),
):
    actor_for(request, admin=True)
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data=request.app.state.identity.account_list(query, page, pageSize))


@router.post("/admin/accounts/{id}", response_model=Envelope[dict])
def update_account(id: str, body: AccountUpdate, request: Request, response: Response):
    actor_for(request, admin=True)
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data=request.app.state.identity.update_account(id, body))


@router.delete("/admin/accounts/{id}", response_model=Envelope[dict])
def delete_account(id: str, request: Request, response: Response):
    actor_for(request, admin=True)
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data=request.app.state.identity.delete_account(id))
