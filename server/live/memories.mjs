import { MemOSClient } from './adapters/memos-client.mjs'
import { toDashboardMemory } from './adapters/memory-mapper.mjs'
import { MemoryService } from './services/memories.mjs'

export function publicMemory(row, preview = false) {
  return toDashboardMemory(row, { maxContentLength: preview ? 8000 : Infinity })
}
export function memoryServiceRoutes(service) {
  return {
    'POST /api/dashboard/project/memory/list': ({ body }) => service.list(body),
    'POST /api/dashboard/project/memory/detail': ({ body }) => service.detail(body.memoryId, body.memType),
    'POST /api/dashboard/project/memory/delete': ({ body }) => service.delete(body.memory_ids, body.memType),
  }
}
export function memoryRoutes(settings, workspaces) {
  return memoryServiceRoutes(new MemoryService(workspaces, new MemOSClient(settings)))
}
