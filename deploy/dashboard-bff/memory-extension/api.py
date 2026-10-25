"""Private Dashboard soft deletion; legacy MCP/product delete semantics are untouched."""
import hmac
import os
from fastapi import APIRouter, Body, Depends, Header, HTTPException
from .soft_delete import delete, ScopeError, RequestError
from .models import catalog, CatalogUnavailable


def install(app, graph, components=None):
    if os.getenv('DASHBOARD_MEMORY_ENABLED') != '1':
        return
    token = os.getenv('DASHBOARD_MEMORY_TOKEN') or os.getenv('DASHBOARD_PROMPT_TOKEN', '')
    if len(token) < 32:
        raise RuntimeError('Private memory management requires a strong token')
    def authorize(x_dashboard_prompt_token: str = Header(default='')):
        if not hmac.compare_digest(x_dashboard_prompt_token, token):
            raise HTTPException(403, 'Memory management forbidden')
    router = APIRouter(prefix='/product/dashboard-memory', dependencies=[Depends(authorize)])
    @router.get('/models')
    def models():
        try:
            return {'code': 200, 'data': catalog(components), 'message': 'success'}
        except CatalogUnavailable:
            raise HTTPException(501, 'Runtime model inventory unavailable for this native deployment') from None
        except Exception:
            raise HTTPException(503, 'Runtime model inventory could not be read') from None
    @router.post('/soft-delete')
    def soft_delete(body: dict = Body(...)):
        if graph.__class__.__name__ != 'Neo4jCommunityGraphDB' or graph.vec_db.__class__.__name__ != 'QdrantVecDB':
            raise HTTPException(501, 'Untested storage backend; no deletion fallback')
        try:
            data = delete(graph, body.get('user_id'), body.get('mem_cube_id'), body.get('memory_ids'))
            return {'code': 200, 'data': data, 'message': 'success'}
        except ScopeError:
            raise HTTPException(404, 'Memory scope validation failed; entire batch unchanged') from None
        except RequestError:
            raise HTTPException(400, 'Invalid soft-delete request') from None
        except Exception:
            raise HTTPException(503, 'Soft-delete synchronization incomplete; retry same private request; no physical deletion fallback') from None
    app.include_router(router)
