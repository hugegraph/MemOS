import { projectRoutes } from './projects.mjs'
import { memoryServiceRoutes } from './memories.mjs'
import { MemOSClient } from './adapters/memos-client.mjs'
import { WorkspaceRepository } from './repositories/workspaces.mjs'
import { WorkspaceService } from './services/workspaces.mjs'
import { MemoryService } from './services/memories.mjs'
import { AuditRepository } from './repositories/audit.mjs'
import { AuditService } from './services/audit.mjs'
import { UsageService } from './services/usage.mjs'
import { auditRoutes } from './audit.mjs'
import { usageServiceRoutes } from './usage.mjs'
import { managementRoutes } from './management.mjs'
import { PromptClient } from './adapters/prompt-client.mjs'
import { PromptService, promptRoutes } from './services/prompts.mjs'

export function buildRoutes(settings, dependencies = {}) {
  const client = dependencies.client ?? new MemOSClient(settings)
  const projects =
    dependencies.workspaces ??
    new WorkspaceService(dependencies.workspaceRepository ?? new WorkspaceRepository(settings), client, settings.owner)
  const memories = dependencies.memories ?? new MemoryService(projects, client)
  const audit = dependencies.audit ?? new AuditService(dependencies.auditRepository ?? new AuditRepository(settings), projects)
  const usage = dependencies.usage ?? new UsageService(audit)
  return {
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
