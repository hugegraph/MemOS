import { ApiError } from '../errors.ts'
import type { AuditReader, DashboardInput, DailyUsage } from '../types.ts'

const DAY = 86400000
function dayTime(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new ApiError('日期格式无效')
  const time = Date.parse(`${value}T00:00:00Z`)
  if (!Number.isFinite(time) || new Date(time).toISOString().slice(0, 10) !== value) throw new ApiError('日期不存在')
  return time
}
export class UsageService {
  declare audit: AuditReader
  constructor(audit: AuditReader) {
    this.audit = audit
  }
  async query(body: DashboardInput) {
    const end = dayTime(body.endDate || new Date().toISOString().slice(0, 10))
    const start = body.startDate ? dayTime(body.startDate) : end - 13 * DAY
    if (start > end || end - start > 366 * DAY) throw new ApiError('日期范围无效或超过 367 天')
    // Always consult the source, even for empty project sets. Read failure is not zero usage.
    const window = (await this.audit.rows()).filter((row) => row.time >= start && row.time < end + DAY)
    const scoped = this.audit.scoped(window, body.projectId)
    // Filtering historical IDs does not depend on an active key: revocation must not erase usage.
    // Neither a selected key nor current registry membership establishes missing historical identity.
    const rows = body.apiKeyId ? scoped.filter((row) => row.cubeId && row.apiKeyId === body.apiKeyId) : scoped
    const days = new Map<string, DailyUsage>()
    for (let time = start; time <= end; time += DAY) {
      const date = new Date(time).toISOString().slice(0, 10)
      days.set(date, {
        date,
        totalCalls: 0,
        callsByEvent: {},
        addMessage: { calls: 0, totalTokens: 0 },
        searchMemory: { calls: 0, totalTokens: 0 },
      })
    }
    for (const row of rows) {
      if (row.time < start || row.time >= end + DAY) continue
      const day = days.get(new Date(row.time).toISOString().slice(0, 10))!
      // Count normalized events, including legacy camelCase records and failed attempts.
      // Never disguise get/delete/feedback calls as add/search to fit the current UI.
      const key = row.eventType
      day.totalCalls++
      day.callsByEvent[key] = (day.callsByEvent[key] ?? 0) + 1
      if (['addMessage', 'searchMemory', 'rerank'].includes(key)) {
        day[key as 'addMessage' | 'searchMemory' | 'rerank'] ??= { calls: 0, totalTokens: 0 }
        day[key as 'addMessage' | 'searchMemory' | 'rerank']!.calls++
      } else if (['chat', 'extractMemory'].includes(key)) {
        day[key as 'chat' | 'extractMemory'] ??= { requestTokens: 0, responseTokens: 0, totalTokens: 0 }
      }
    }
    return {
      dailyStats: [...days.values()],
      auditScope: {
        ...this.audit.scopeMetadata(window, body.projectId),
        unattributedKeyRecords: scoped.filter((row) => !row.apiKeyId).length,
      },
    }
  }
}
