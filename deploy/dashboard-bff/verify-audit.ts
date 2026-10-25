// Run INSIDE the new BFF container. Read-only independent oracle; never prints raw bodies/tokens.
import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { join } from 'node:path'
import type { CheckedReport, NativeAudit, Envelope, DashboardResult } from '../../scripts/tool-types.ts'
import type { WorkspaceRecord } from '../../server/live/types.ts'

const report: CheckedReport = { kind: 'real-audit-acceptance', status: 'FAIL', checks: [] }
let stage = 'configuration'
try {
  const base = process.env.AUDIT_VERIFY_URL || 'http://127.0.0.1:5281'
  const projectId = process.env.AUDIT_VERIFY_PROJECT || 'dashboard-demo'
  const project = (JSON.parse(process.env.DASHBOARD_WORKSPACES!) as WorkspaceRecord[]).find((p) => p.id === projectId)
  assert(project)
  const raw: NativeAudit[] = []
  stage = 'raw-audit-read'
  for (const name of await readdir(process.env.MEMORY_CALL_LOG_DIR!)) {
    if (!/^calls-.*\.jsonl$/.test(name)) continue
    const lines = (await readFile(join(process.env.MEMORY_CALL_LOG_DIR!, name), 'utf8')).split('\n').slice(0, -1)
    for (const line of lines) {
      try {
        const r: NativeAudit = JSON.parse(line)
        if (r.tenant === project.tenant && Number.isFinite(Date.parse(r.ts))) raw.push(r)
      } catch {
        /* malformed records are counted separately by BFF tests */
      }
    }
  }
  assert(raw.length > 0 && raw.length <= 30, 'Choose a bounded, nonempty fixture')
  async function call<P extends string>(path: P, body?: unknown): Promise<DashboardResult<P>> {
    const r = await fetch(base + path, {
      method: body ? 'POST' : 'GET',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(5000),
    })
    assert.equal(r.status, 200)
    const result = (await r.json()) as Envelope<DashboardResult<P>>
    assert.equal(result.code, 0)
    return result.data
  }
  stage = 'meta'
  assert.equal((await call('/api/meta')).mode, 'live')
  const dates = raw.map((r) => new Date(r.ts).toISOString().slice(0, 10)).sort()
  const startDate = dates[0],
    endDate = dates.at(-1)!
  const query = { projectId, startTime: `${startDate}T00:00:00Z`, endTime: `${endDate}T23:59:59.999Z`, pageNum: 1, pageSize: 100 }
  const events: Record<string, string> = {
    add_message: 'addMessage',
    search_memory: 'searchMemory',
    get_user_profile: 'getMemory',
    delete_memory: 'deleteMemory',
    add_feedback: 'addFeedback',
  }
  const normalized = (value: unknown): unknown => {
    if (typeof value === 'string') {
      try {
        return JSON.parse(value)
      } catch {
        return value
      }
    }
    return value ?? null
  }
  stage = 'list-and-details'
  const list = await call('/api/dashboard/log/list', query)
  assert.equal(list.total, raw.length)
  assert.equal(list.logs.length, raw.length)
  assert.equal(new Set(list.logs.map((r) => r.id)).size, raw.length)
  const remaining = [...raw]
  for (const row of list.logs) {
    const detail = await call('/api/dashboard/log/detail', { logId: row.id })
    for (const key of ['id', 'eventType', 'createTime', 'duration', 'status']) assert.equal(row[key], detail[key])
    const index = remaining.findIndex((r) => {
      try {
        assert.equal(detail.eventType, events[r.tool] || r.tool)
        assert.equal(detail.createTime, new Date(r.ts).toISOString())
        assert.equal(detail.duration, Number(r.ms ?? 0))
        assert.equal(detail.status, r.ok === true ? 'SUCCESS' : 'FAILED')
        assert.deepEqual(JSON.parse(detail.requestBody!), normalized(r.input))
        assert.deepEqual(JSON.parse(detail.responseBody!), normalized(r.output))
        return true
      } catch {
        return false
      }
    })
    assert(index >= 0)
    remaining.splice(index, 1)
  }
  report.checks.push({ name: 'raw JSONL ↔ live list/detail', records: raw.length, status: 'PASS' })
  stage = 'daily-usage'
  const result = await call('/api/dashboard/usage/query', { projectId, startDate, endDate })
  const expectedDates = Array.from({ length: (Date.parse(endDate) - Date.parse(startDate)) / 86400000 + 1 }, (_, i) =>
    new Date(Date.parse(startDate) + i * 86400000).toISOString().slice(0, 10),
  )
  assert.deepEqual(
    result.dailyStats.map((day) => day.date),
    expectedDates,
  )
  for (const day of result.dailyStats)
    for (const [tool, key] of Object.entries({ add_message: 'addMessage', search_memory: 'searchMemory', rerank: 'rerank' } as const)) {
      assert.equal(day[key]!.calls, raw.filter((r) => r.tool === tool && new Date(r.ts).toISOString().slice(0, 10) === day.date).length)
      assert.equal(day[key]!.totalTokens, 0)
    }
  report.checks.push({ name: 'daily calls independently counted', days: result.dailyStats.length, status: 'PASS' })
  if (process.env.AUDIT_EXPECT_CURRENT) {
    stage = 'fresh-readonly-MCP-event'
    const fresh = raw.filter(
      (r) =>
        r.tool === 'get_user_profile' &&
        (normalized(r.input) as { current?: unknown } | null)?.current === Number(process.env.AUDIT_EXPECT_CURRENT),
    )
    assert.equal(fresh.length, 1)
    assert.equal(fresh[0].ok, true)
    report.checks.push({ name: 'fresh authenticated read-only MCP event reached BFF', status: 'PASS' })
  }
  report.status = 'PASS'
} catch (error) {
  report.failure = { stage, type: (error as Error).name } // Never serialize AssertionError.actual/expected or sensitive messages.
}
console.log(JSON.stringify(report, null, 2))
process.exitCode = report.status === 'PASS' ? 0 : 1
