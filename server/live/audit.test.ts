import assert from 'node:assert/strict'
import { mkdtemp, writeFile, appendFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { auditIndex } from './audit.ts'
import { usageRoutes } from './usage.ts'
import type { TestContext } from 'node:test'
import type { OpenApiSpec } from './types.ts'

async function setup(t: TestContext) {
  const auditDir = await mkdtemp(join(tmpdir(), 'memos-audit-'))
  t.after(() => rm(auditDir, { recursive: true, force: true }))
  const projects = {
    get: (id: string) => ({ tenant: id, cubeId: id }),
    all: () => [
      { tenant: 'A', cubeId: 'A' },
      { tenant: 'B', cubeId: 'B' },
    ],
  }
  return { audit: auditIndex({ auditDir }, projects), file: join(auditDir, 'calls-20261001.jsonl') }
}
const row = (tenant = 'A', i = 0) => ({
  ts: '2026-10-01T12:00:00Z',
  tool: 'search_memory',
  tenant,
  cube_id: tenant,
  ms: i,
  ok: i % 2 === 0,
  input: { query: `marker-${i}` },
  output: { found: i },
})
test('audit: append/partial lines/bad records/stable IDs/time/project filters', async (t) => {
  const { audit, file } = await setup(t)
  await writeFile(file, `${JSON.stringify(row())}\nBAD\n${JSON.stringify(row('B'))}\n{"ts":`)
  const initial = await audit.rows()
  assert.equal(initial.length, 2)
  assert.equal(audit.badLines(), 1)
  const id = initial.find((r) => r.raw.tenant === 'A')!.id
  await appendFile(file, '"2026-10-02T12:00:00Z","tenant":"A","cube_id":"A","tool":"add_message","ok":true}\n')
  assert.equal((await audit.rows()).length, 3)
  assert.ok((await audit.rows()).some((r) => r.id === id))
  const list = await audit.routes['POST /api/dashboard/log/list']({
    body: { projectId: 'A', startTime: '2026-10-02T00:00:00Z', endTime: '2026-10-02T23:59:59Z' },
  })
  assert.equal(list.total, 1)
  const detail = await audit.routes['POST /api/dashboard/log/detail']({ body: { logId: list.logs[0].id } })
  assert.equal(detail.requestBody, 'null')
  assert.equal(detail.responseBody, 'null')
  await writeFile(file, `${JSON.stringify(row('B', 99))}\n`)
  assert.equal((await audit.rows()).length, 1)
})
test('usage: real JSONL counts, empty days and tenant isolation', async (t) => {
  const { audit, file } = await setup(t)
  await writeFile(file, [row('A'), row('A', 1), row('B')].map((r) => JSON.stringify(r)).join('\n') + '\n')
  const query = usageRoutes(audit)['POST /api/dashboard/usage/query']
  for (const [projectId, expected] of [
    ['A', 2],
    ['B', 1],
    ['', 3],
    ['all', 3],
  ] as const) {
    const result = await query({ body: { projectId, startDate: '2026-09-30', endDate: '2026-10-02' } })
    assert.deepEqual(
      result.dailyStats.map((d) => d.searchMemory.calls),
      [0, expected, 0],
    )
    assert.ok(result.dailyStats.every((d) => d.searchMemory.totalTokens === 0))
  }
  const unknown = await query({ body: { apiKeyId: 'key', startDate: '2026-10-01', endDate: '2026-10-01' } })
  assert.equal(unknown.dailyStats[0].totalCalls, 0)
  assert.equal(unknown.auditScope.unattributedKeyRecords, 3)
})
test('usage: all accepted event categories reconcile, failures count, aliases normalize, empty days stay empty', async (t) => {
  const { audit, file } = await setup(t)
  const { readFile } = await import('node:fs/promises')
  const spec: OpenApiSpec = JSON.parse(await readFile(new URL('../../openapi.json', import.meta.url), 'utf8'))
  const events = spec.components.schemas.EventType.enum
  const aliases: Record<string, string> = {
    addMessage: 'add_message',
    searchMemory: 'search_memory',
    getMemory: 'get_user_profile',
    deleteMemory: 'delete_memory',
    addFeedback: 'add_feedback',
  }
  const raw = events.flatMap((event, i) => [
    { ...row('A', i), tool: aliases[event] ?? event },
    { ...row('A', i + 1), tool: event, ok: false },
  ])
  // Same-day foreign tenant and out-of-range records must not affect the result.
  raw.push(row('B'), { ...row('A'), ts: '2026-10-03T00:00:00Z' })
  await writeFile(file, raw.map((r) => JSON.stringify(r)).join('\n') + '\n')
  const query = usageRoutes(audit)['POST /api/dashboard/usage/query']
  const { dailyStats } = await query({ body: { projectId: 'A', startDate: '2026-09-30', endDate: '2026-10-02' } })
  const [before, active, after] = dailyStats
  assert.equal(active.totalCalls, events.length * 2)
  assert.deepEqual(active.callsByEvent, Object.fromEntries(events.map((event) => [event, 2])))
  assert.equal(
    Object.values(active.callsByEvent).reduce((a, b) => a + b, 0),
    active.totalCalls,
  )
  assert.equal(active.addMessage.calls, 2)
  assert.equal(active.searchMemory.calls, 2)
  assert.equal(active.rerank!.calls, 2)
  for (const day of [before, after]) {
    assert.equal(day.totalCalls, 0)
    assert.deepEqual(day.callsByEvent, {})
    assert.equal(day.chat, undefined)
    assert.equal(day.extractMemory, undefined)
    assert.equal(day.rerank, undefined)
    // Regression: presence of zero-token objects used to create three phantom calls.
    assert.equal(day.addMessage.calls + day.searchMemory.calls + Number(!!day.chat) + Number(!!day.extractMemory) + Number(!!day.rerank), 0)
  }
  const invalid = { ...row('A'), tool: 'unknown_event' }
  await appendFile(file, JSON.stringify(invalid) + '\n')
  assert.equal(
    (await query({ body: { projectId: 'A', startDate: '2026-10-01', endDate: '2026-10-01' } })).dailyStats[0].totalCalls,
    active.totalCalls,
  )
  assert.equal(audit.badLines(), 1)
})
test('audit: 10000 actual JSONL lines first-page response below 1s', async (t) => {
  const { audit, file } = await setup(t)
  await writeFile(file, Array.from({ length: 10000 }, (_, i) => JSON.stringify(row('A', i))).join('\n') + '\n')
  const start = performance.now()
  const result = await audit.routes['POST /api/dashboard/log/list']({ body: { projectId: 'A', pageNum: 1, pageSize: 10 } })
  const ms = performance.now() - start
  t.diagnostic(`Cold JSONL parse + query: ${ms.toFixed(1)}ms, 10000 short lines (not HTTP/browser timing)`)
  assert.equal(result.total, 10000)
  assert.equal(result.logs.length, 10)
  assert.ok(ms < 1000, `${ms}ms exceeds 1 second`)
})
