import { projectRoutes } from './projects.ts'
import { join } from 'node:path'
import { memoryServiceRoutes } from './memories.ts'
import { MemOSClient } from './adapters/memos-client.ts'
import { WorkspaceRepository } from './repositories/workspaces.ts'
import { WorkspaceService } from './services/workspaces.ts'
import { MemoryService } from './services/memories.ts'
import { AuditRepository } from './repositories/audit.ts'
import { AuditService } from './services/audit.ts'
import { UsageService } from './services/usage.ts'
import { auditRoutes } from './audit.ts'
import { usageServiceRoutes } from './usage.ts'
import { managementRoutes } from './management.ts'
import { PromptClient } from './adapters/prompt-client.ts'
import { ModelsClient } from './adapters/models-client.ts'
import { PromptService, promptRoutes } from './services/prompts.ts'
import type { KeyService } from './services/keys.ts'
import type { Settings, NativeClient, WorkspaceStorage, AuditStorage, GatewayConfig, ScopedPromptClient, Routes } from './types.ts'

export interface Dependencies {
  client?: NativeClient
  workspaces?: WorkspaceService
  workspaceRepository?: WorkspaceStorage
  memories?: Pick<MemoryService, 'list' | 'detail' | 'delete'>
  audit?: Pick<AuditService, 'rows' | 'badLines' | 'scoped' | 'scopeMetadata' | 'list' | 'detail'> & { routes?: Routes }
  auditRepository?: AuditStorage
  usage?: Pick<UsageService, 'query'>
  keys?: Pick<KeyService, 'list' | 'create' | 'copy' | 'revoke'>
  gatewayConfig?: GatewayConfig
  prompts?: Pick<PromptService, 'execute'>
  promptClient?: ScopedPromptClient
  models?: Pick<ModelsClient, 'list'>
}

export function buildRoutes(settings: Settings, dependencies: Dependencies = {}): Routes {
  const client = dependencies.client ?? new MemOSClient(settings)
  const projects =
    dependencies.workspaces ??
    new WorkspaceService(
      dependencies.workspaceRepository ?? new WorkspaceRepository(settings),
      client,
      settings.owner,
      join(settings.dataDir, 'workspace-create-recovery.json'),
    )
  const memories = dependencies.memories ?? new MemoryService(projects, client)
  const audit = dependencies.audit ?? new AuditService(dependencies.auditRepository ?? new AuditRepository(settings), projects)
  const usage = dependencies.usage ?? new UsageService(audit)
  return {
    'GET /api/dashboard/models': () => (dependencies.models ?? new ModelsClient(client, settings.memoryToken)).list(),
    ...projectRoutes(projects),
    ...memoryServiceRoutes(memories),
    ...(audit.routes ?? auditRoutes(audit)),
    ...usageServiceRoutes(usage),
    ...managementRoutes(settings, projects, dependencies),
    ...promptRoutes(
      dependencies.prompts ?? new PromptService(projects, dependencies.promptClient ?? new PromptClient(client, settings.promptToken)),
    ),
  }
}
