import { AuditRepository } from './repositories/audit.mjs'
import { AuditService } from './services/audit.mjs'
import { toLogDetail } from './adapters/audit-mapper.mjs'

export function auditRoutes(service) {
  return {
    'POST /api/dashboard/log/list': ({ body }) => service.list(body),
    'POST /api/dashboard/log/detail': ({ body }) => service.detail(body.logId),
  }
}
export function auditIndex(settings, projects) {
  const service = new AuditService(new AuditRepository(settings), projects)
  return {
    rows: () => service.rows(),
    scoped: (rows, projectId) => service.scoped(rows, projectId),
    scopeMetadata: (rows, projectId) => service.scopeMetadata(rows, projectId),
    detail: toLogDetail,
    badLines: () => service.badLines(),
    routes: auditRoutes(service),
  }
}
