import { MemOSClient } from './adapters/memos-client.ts'
import { toDashboardMemory } from './adapters/memory-mapper.ts'
import { MemoryService } from './services/memories.ts'
import type { Settings, Routes, WorkspaceReader, NativeMemory } from './types.ts'

export function publicMemory(row: NativeMemory, preview = false) {
  return toDashboardMemory(row, { maxContentLength: preview ? 8000 : Infinity })
}
export function memoryServiceRoutes(service: Pick<MemoryService, 'list' | 'detail' | 'delete'>): Routes {
  return {
    'POST /api/dashboard/project/memory/list': ({ body }) => service.list(body),
    'POST /api/dashboard/project/memory/detail': ({ body }) => service.detail(body.memoryId!, body.memType),
    'POST /api/dashboard/project/memory/delete': ({ body }) => service.delete(body.memory_ids!, body.memType),
  }
}
export function memoryRoutes(settings: Settings, workspaces: WorkspaceReader) {
  return memoryServiceRoutes(new MemoryService(workspaces, new MemOSClient(settings)))
}
