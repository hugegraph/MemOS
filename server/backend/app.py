"""Application factory: independent of model SDKs, databases, and Mock."""

import logging
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response

from .audit_routes import AuditService
from .audit_routes import router as audit_router
from .audit_writer import ConsoleAuditMiddleware, ConsoleWriter
from .auth import SessionMiddleware
from .auth import router as auth_router
from .compatibility import router as compatibility_router
from .control import ControlStore
from .errors import install_error_handlers
from .identity_store import IdentityStore
from .invites import InviteStore
from .invites import router as invite_router
from .kb_store import KnowledgeStore
from .key_store import ApiKeyStore, sync_manifest
from .keys import router as keys_router
from .knowledge import router as knowledge_router
from .knowledge_files import router as kb_file_router
from .members import router as member_router
from .memories import router as memory_router
from .memory_lifecycle import router as lifecycle_router
from .memory_upstream import MemoryUpstream
from .models import SERVICE_NAME, Envelope, Meta
from .product_client import ProductClient
from .profile_templates import router as template_router
from .projects import router as project_router
from .prompts import router as prompt_router
from .read_views import router as read_router
from .settings import Settings
from .upstream import UpstreamClient
from .usage_routes import router as usage_router


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    config = settings if settings is not None else Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient(
            base_url=config.upstream_url,
            timeout=config.upstream_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        ) as client:
            app.state.upstream = UpstreamClient(client)
            app.state.memories = MemoryUpstream(client)
            app.state.product = ProductClient(client)
            app.state.settings = config
            app.state.control = ControlStore(config)
            app.state.identity = IdentityStore(app.state.control, config)
            app.state.knowledge = KnowledgeStore(app.state.control)
            app.state.invites = InviteStore(app.state.identity)
            app.state.audit = AuditService(config)
            app.state.console_audit = ConsoleWriter(
                config.audit_console_dir or config.data_dir / "audit"
            )
            if config.gateway_keys_file:
                try:
                    sync_manifest(ApiKeyStore(app.state.control), config.gateway_keys_file)
                except (OSError, sqlite3.Error) as error:
                    logging.getLogger(__name__).warning(
                        "gateway keys manifest self-heal failed; later writes will "
                        "report the real error: %r",
                        error,
                    )
            yield

    app = FastAPI(title="MemOS Dashboard Backend", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    app.add_middleware(ConsoleAuditMiddleware)
    app.add_middleware(SessionMiddleware)
    app.include_router(memory_router)
    app.include_router(lifecycle_router)
    app.include_router(audit_router)
    app.include_router(usage_router)
    app.include_router(project_router)
    app.include_router(auth_router)
    app.include_router(member_router)
    app.include_router(invite_router)
    app.include_router(knowledge_router)
    app.include_router(kb_file_router)
    app.include_router(prompt_router)
    app.include_router(keys_router)
    app.include_router(read_router)
    app.include_router(template_router)
    app.include_router(compatibility_router)

    @app.get("/health", response_model=Envelope[dict[str, str]])
    async def liveness(response: Response) -> Envelope[dict[str, str]]:
        response.headers["Cache-Control"] = "no-store"
        return Envelope(data={"status": "healthy", "service": SERVICE_NAME})

    @app.get("/api/meta", response_model=Envelope[Meta])
    async def meta(request: Request, response: Response) -> Envelope[Meta]:
        health = await request.app.state.upstream.health()
        response.headers["Cache-Control"] = "no-store"
        return Envelope(data=Meta(health=health, authEnabled=config.auth_enabled))

    return app
