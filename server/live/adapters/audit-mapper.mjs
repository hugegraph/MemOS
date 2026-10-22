import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'

const spec = JSON.parse(readFileSync(new URL('../../../openapi.json', import.meta.url), 'utf8'))
const allowedEvents = new Set(spec.components.schemas.EventType.enum)
const events = {
  add_message: 'addMessage',
  search_memory: 'searchMemory',
  get_user_profile: 'getMemory',
  delete_memory: 'deleteMemory',
  add_feedback: 'addFeedback',
}
const jsonValue = (value) => {
  if (typeof value === 'string') {
    try {
      return JSON.parse(value)
    } catch {
      return value
    }
  }
  return value ?? null
}

/** Input protocol: gateway/audit/tool_log.py. Output keeps raw for independent usage aggregation. */
export function normalizeAuditRecord(raw, source) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('审计记录不是对象')
  const time = typeof raw.ts === 'string' ? Date.parse(raw.ts) : NaN
  if (!Number.isFinite(time) || typeof raw.tenant !== 'string' || !raw.tenant.trim()) throw new Error('审计缺少时间或租户')
  const eventType = events[raw.tool] || raw.tool
  if (!allowedEvents.has(eventType)) throw new Error('未识别审计事件，不伪装为其他操作')
  const id = createHash('sha256').update(`${source.name}:${source.offset}:`).update(source.line).digest('hex').slice(0, 32)
  const input = jsonValue(raw.input)
  return {
    id,
    raw,
    time,
    eventType,
    tenant: raw.tenant,
    cubeId: typeof raw.cube_id === 'string' && raw.cube_id.trim() ? raw.cube_id : undefined,
    apiKeyId: typeof raw.api_key_id === 'string' && /^[0-9a-f]{24}$/.test(raw.api_key_id) ? raw.api_key_id : undefined,
    traceId: typeof raw.trace_id === 'string' && raw.trace_id ? raw.trace_id : id,
    userId: typeof input?.user_id === 'string' ? input.user_id : raw.tenant,
    agentId: typeof input?.agent_id === 'string' ? input.agent_id : '',
    duration: Number.isFinite(Number(raw.ms)) ? Math.max(0, Number(raw.ms)) : 0,
    status: raw.ok === true ? 'SUCCESS' : 'FAILED',
  }
}
/** @returns {import('../../../src/lib/api').RequestLog} */
export function toRequestLog(row) {
  return {
    id: row.id,
    createTime: new Date(row.time).toISOString(),
    eventType: row.eventType,
    status: row.status,
    duration: row.duration,
    userIds: [row.userId],
    agentIds: row.agentId ? [row.agentId] : [],
    cubeId: row.cubeId ?? null,
    attribution: row.cubeId ? 'cube' : 'unknown',
  }
}
/** Bodies are materialized only for a detail request, not for every row on a list request. */
export function toLogDetail(row) {
  const { userIds: _users, agentIds: _agents, ...base } = toRequestLog(row)
  void _users
  void _agents
  return {
    ...base,
    traceId: row.traceId,
    userId: row.userId,
    agentId: row.agentId,
    requestTokens: 0,
    responseTokens: 0,
    requestCode: 0,
    responseCode: row.status === 'SUCCESS' ? 200 : 500,
    requestBody: JSON.stringify(jsonValue(row.raw.input)),
    responseBody: JSON.stringify(jsonValue(row.raw.output)),
  }
}
