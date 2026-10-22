import { ApiError } from '../errors.mjs'

/**
 * Dashboard Project stays defined in src/lib/api.ts / openapi.json.
 * This binding is internal; principal mirrors gateway.domain.tenants.Tenant.
 * @typedef {{projectId: string, principal: {userId: string, cubeId: string}, auditTenant: string}} WorkspaceBinding
 */
export function workspaceBinding(project) {
  const { id, userId, cubeId, tenant = userId } = project || {}
  if (![id, userId, cubeId, tenant].every((v) => typeof v === 'string' && v.trim()))
    throw new ApiError('工作区身份配置无效，需要明确 projectId/userId/cubeId/tenant', 50300, 503)
  return Object.freeze({ projectId: id, principal: Object.freeze({ userId, cubeId }), auditTenant: tenant })
}

/** @returns {import('../../../src/lib/api').Project} */
export function toDashboardProject({ id, name, description = '', status = 'DISABLED', createTime, isMultiView = 0 }) {
  return { id, name, description, status, createTime, isMultiView }
}
