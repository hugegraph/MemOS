import { MemOSClient } from './adapters/memos-client.mjs'
import { WorkspaceRepository } from './repositories/workspaces.mjs'
import { WorkspaceService } from './services/workspaces.mjs'

export function projectRoutes(service) {
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
export function projectStore(settings) {
  const service = new WorkspaceService(new WorkspaceRepository(settings), new MemOSClient(settings), settings.owner)
  return {
    get: (id) => service.get(id),
    all: () => service.all(),
    resolveWorkspace: (id) => service.resolveWorkspace(id),
    routes: projectRoutes(service),
  }
}
