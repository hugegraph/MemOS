import { UsageService } from './services/usage.ts'
import type { AuditReader, Routes, RouteContext } from './types.ts'

export function usageRoutes(audit: AuditReader) {
  return usageServiceRoutes(new UsageService(audit))
}
export function usageServiceRoutes(service: Pick<UsageService, 'query'>) {
  return { 'POST /api/dashboard/usage/query': ({ body }: Pick<RouteContext, 'body'>) => service.query(body) } satisfies Routes
}
