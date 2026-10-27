"""Opaque persisted project preference, deliberately separate from future authentication."""

import hashlib
import re
import secrets
import time

COOKIE = "memos_project_context"


def context_key(request):
    token = request.cookies.get(COOKIE, "")
    return (
        hashlib.sha256(token.encode()).hexdigest()
        if re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
        else None
    )


def ensure_context(request, response):
    if request.scope.get("identity"):
        return request.scope["identity"]["session_key"]
    store = request.app.state.control
    key = context_key(request)
    with store.connection() as db:
        valid = (
            key
            and db.execute(
                "SELECT 1 FROM project_contexts WHERE token_hash=? AND expires_at>?",
                (key, int(time.time())),
            ).fetchone()
        )
    if valid:
        return key
    token = secrets.token_urlsafe(32)
    key = store.ensure_context(token)
    response.set_cookie(
        COOKIE,
        token,
        max_age=30 * 86400,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return key


def selected_project(request):
    from .auth import control_for

    actor = request.scope.get("identity")
    return control_for(request).current(actor["session_key"] if actor else context_key(request))
