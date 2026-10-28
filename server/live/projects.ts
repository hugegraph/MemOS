import { MemOSClient } from './adapters/memos-client.ts'
import { WorkspaceRepository } from './repositories/workspaces.ts'
import { WorkspaceService } from './services/workspaces.ts'
import type { Settings, Routes } from './types.ts'

export function projectRoutes(service: Pick<WorkspaceService, 'dict' | 'list' | 'create' | 'update' | 'revoke'>): Routes {
  const prefix = 'POST /api/dashboard/project/'
  return {
    'GET /api/dashboard/project/dict': () => service.dict(),
    [`${prefix}list`]: ({ body }) => service.list(body),
    [`${prefix}create`]: ({ body }) => service.create(body),
    [`${prefix}update`]: ({ body }) => service.update(body),
    [`${prefix}revoke/{id}`]: ({ params }) => service.revoke(params[0]),
  }
}

// Compatibility facade; the composition root injects a shared client instead.
export function projectStore(settings: Settings) {
  const service = new WorkspaceService(new WorkspaceRepository(settings), new MemOSClient(settings), settings.owner)
  return {
    get: (id: unknown) => service.get(id),
    all: () => service.all(),
    resolveWorkspace: (id: unknown) => service.resolveWorkspace(id),
    routes: projectRoutes(service),
  }
}
