"""Compose native tools with file-backed authentication and trusted audit identity.

Original tools, identity resolution, backend and public pages remain reused.
Native tool logging is reused with a trusted cube-id sink decorator.
This entrypoint always enables auth, even when all tokens have been revoked.
"""
import os
import hashlib
import sys
sys.path.insert(0, '/gateway')

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from gateway.app import App, INSTRUCTIONS
from gateway.audit.facade import Auditor
from gateway.audit.tool_log import ToolLogger
from gateway.backend import ProductClient
from gateway.config import settings
from gateway.domain.tenants import TenantRegistry
from gateway.identity import IdentityResolver
from gateway.mcp_api.http import PublicPages
from gateway.mcp_api.tools import register as register_tools
from gateway.service import MemoryService
from file_registry import FileTenantRegistry
from scoped_audit import ScopedAuditSink


class FileTokenVerifier(StaticTokenVerifier):
    def __init__(self, registry):
        super().__init__(tokens={})
        self.registry = registry

    async def verify_token(self, token):
        # One coherent file snapshot per authentication. No shared mutable token table.
        claims = self.registry.verifier_tokens().get(token)
        if not claims:
            return None
        verified = await StaticTokenVerifier(tokens={token: claims}).verify_token(token)
        if verified is not None:
            # Exactly KeyService's public ID, not the secret and not a user-supplied claim.
            verified.claims = {**(verified.claims or {}),
                               'dashboard_api_key_id': hashlib.sha256(token.encode('utf-8')).hexdigest()[:24]}
        return verified


def build_file_app(path):
    tenants = FileTenantRegistry(path, TenantRegistry)
    tenants.validate() # Invalid configuration cannot start an unauthenticated gateway.
    identity = IdentityResolver(settings)
    memory = MemoryService(ProductClient(settings), settings)
    auditor = Auditor(settings, tenants, identity)
    auditor.tool_logger = ToolLogger(
        calls=ScopedAuditSink(auditor.calls, identity, get_access_token),
        searches=ScopedAuditSink(auditor.searches, identity, get_access_token),
        peek_tenant=identity.peek_user_id,
    )
    mcp = FastMCP(name=settings.service_name, instructions=INSTRUCTIONS,
                  auth=FileTokenVerifier(tenants), stateless_http=True)
    mcp.add_middleware(auditor.rpc_middleware)
    register_tools(mcp, auditor.tool_logger, memory, identity)
    PublicPages(settings, tenants, mcp).register(mcp)
    return App(settings=settings, tenants=tenants, identity=identity,
               memory=memory, auditor=auditor, mcp=mcp)


if __name__ == '__main__':
    app = build_file_app(os.environ['DASHBOARD_GATEWAY_ENV'])
    app.mcp.run(transport='http', host='0.0.0.0', port=8003,
                middleware=[app.http_middleware()])
