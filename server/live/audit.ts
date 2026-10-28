import { AuditRepository } from './repositories/audit.ts'
import { AuditService } from './services/audit.ts'
import { toLogDetail } from './adapters/audit-mapper.ts'
import type { Settings, AuditWorkspaces, Routes, RouteContext, AuditRecord } from './types.ts'

export function auditRoutes(service: Pick<AuditService, 'list' | 'detail'>) {
  return {
    'POST /api/dashboard/log/list': ({ body }: Pick<RouteContext, 'body'>) => service.list(body),
    'POST /api/dashboard/log/detail': ({ body }: Pick<RouteContext, 'body'>) => service.detail(body.logId),
  } satisfies Routes
}
export function auditIndex(settings: Pick<Settings, 'auditDir'>, projects: AuditWorkspaces) {
  const service = new AuditService(new AuditRepository(settings), projects)
  return {
    rows: () => service.rows(),
    scoped: (rows: AuditRecord[], projectId?: string) => service.scoped(rows, projectId),
    scopeMetadata: (rows: AuditRecord[], projectId?: string) => service.scopeMetadata(rows, projectId),
    detail: toLogDetail,
    badLines: () => service.badLines(),
    routes: auditRoutes(service),
  }
}
