import { UsageService } from './services/usage.mjs'

export function usageRoutes(audit) {
  return usageServiceRoutes(new UsageService(audit))
}
export function usageServiceRoutes(service) {
  return { 'POST /api/dashboard/usage/query': ({ body }) => service.query(body) }
}
