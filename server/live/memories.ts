import type { MemoryService } from './services/memories.ts'
import type { Routes } from './types.ts'
export function memoryServiceRoutes(service: Pick<MemoryService, 'list' | 'detail' | 'delete'>): Routes {
  return {
    'POST /api/dashboard/project/memory/list': ({ body }) => service.list(body),
    'POST /api/dashboard/project/memory/detail': ({ body }) => service.detail(body.memoryId!, body.memType),
    'POST /api/dashboard/project/memory/delete': ({ body }) => service.delete(body.memory_ids!, body.memType),
  }
}
