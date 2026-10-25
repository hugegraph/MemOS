import { ApiError } from '../errors.ts'
import type { Project } from '../../../shared/contract.ts'
import type { WorkspaceBinding, WorkspaceRecord } from '../types.ts'

/**
 * Dashboard Project stays defined in shared/contract.ts / openapi.json.
 * This binding is internal; principal mirrors gateway.domain.tenants.Tenant.
 */
export function workspaceBinding(project: unknown): WorkspaceBinding {
  const { id, userId, cubeId, tenant = userId } = (project || {}) as WorkspaceRecord
  if (![id, userId, cubeId, tenant].every((v) => typeof v === 'string' && v.trim()))
    throw new ApiError('工作区身份配置无效，需要明确 projectId/userId/cubeId/tenant', 50300, 503)
  return Object.freeze({ projectId: id, principal: Object.freeze({ userId, cubeId }), auditTenant: tenant })
}

/** Return only public fields from the shared Project contract. */
export function toDashboardProject({ id, name, description = '', status = 'DISABLED', createTime, isMultiView = 0 }: Project): Project {
  return { id, name, description, status, createTime, isMultiView }
}
