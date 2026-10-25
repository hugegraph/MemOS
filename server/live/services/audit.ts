import { ApiError } from '../errors.ts'
import { paginate, timeRange } from '../query.ts'
import { toRequestLog, toLogDetail } from '../adapters/audit-mapper.ts'
import type { AuditStorage, AuditWorkspaces, AuditRecord, DashboardInput, Routes, Page, AttributedLog, AuditScope } from '../types.ts'

export class AuditService {
  declare routes?: Routes
  declare repository: AuditStorage
  declare workspaces: AuditWorkspaces
  constructor(repository: AuditStorage, workspaces: AuditWorkspaces) {
    this.repository = repository
    this.workspaces = workspaces
  }
  rows() {
    return this.repository.rows()
  }
  badLines() {
    return this.repository.badLines()
  }
  scoped(rows: AuditRecord[], projectId?: string) {
    // Current registry cardinality does not establish historical cube ownership.
    const projects = projectId && projectId !== 'all' ? [this.workspaces.get(projectId)] : this.workspaces.all()
    const global = !projectId || projectId === 'all'
    return rows.filter((row) => projects.some((p) => p.tenant === row.tenant && (row.cubeId ? p.cubeId === row.cubeId : global)))
  }
  scopeMetadata(rows: AuditRecord[], projectId?: string) {
    const projects = projectId && projectId !== 'all' ? [this.workspaces.get(projectId)] : this.workspaces.all()
    const tenants = new Set(projects.map((p) => p.tenant))
    return {
      mode: projectId && projectId !== 'all' ? 'cube' : 'configured-tenants',
      unattributedRecords: rows.filter((r) => !r.cubeId && tenants.has(r.tenant)).length,
    }
  }
  async list(query: DashboardInput): Promise<Page<AttributedLog, 'logs'> & { auditScope: AuditScope }> {
    const inRange = timeRange(query.startTime, query.endTime)
    const window = (await this.rows()).filter((row) => inRange(new Date(row.time).toISOString()))
    let data = this.scoped(window, query.projectId)
    if (query.eventType) data = data.filter((row) => row.eventType === query.eventType)
    if (query.status) data = data.filter((row) => row.status.toUpperCase() === query.status!.toUpperCase())
    if (query.subjectId && ['user', 'agent'].includes(query.subjectType!))
      data = data.filter((row) => row[query.subjectType === 'user' ? 'userId' : 'agentId'] === query.subjectId)
    if (query.query) {
      const q = query.query.toLowerCase()
      data = data.filter((row) => `${row.traceId} ${row.userId} ${row.agentId}`.toLowerCase().includes(q))
    }
    const result = paginate(data, query, 'logs') as Page<AuditRecord | AttributedLog, 'logs'> & { auditScope?: AuditScope }
    result.logs = (result.logs as AuditRecord[]).map(toRequestLog)
    result.auditScope = this.scopeMetadata(window, query.projectId)
    return result as Page<AttributedLog, 'logs'> & { auditScope: AuditScope }
  }
  async detail(id: unknown) {
    const row = this.scoped(await this.rows()).find((row) => row.id === id)
    if (!row) throw new ApiError('日志不存在', 40001, 404)
    return toLogDetail(row)
  }
}
