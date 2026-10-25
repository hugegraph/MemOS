// Read-only: independently collected raw JSONL counts, never the production mapper.
import assert from 'node:assert/strict'
import { readFile, writeFile } from 'node:fs/promises'
import type { Envelope, UsageResult } from '../../scripts/tool-types.ts'
interface UsageOracle {
  kind: string
  projectId?: string
  attribution?: string
  cubeId?: string
  startDate: string
  endDate: string
  days: Record<string, Record<string, number>>
  total: number
  emptyDate: string
  emptyRawCount: number
  byEvent: Record<string, number>
}

const oracle: UsageOracle = JSON.parse((await readFile(process.env.USAGE_ORACLE!, 'utf8')).replace(/^\uFEFF/, ''))
assert.equal(oracle.kind, 'raw-jsonl-usage-oracle')
if (oracle.projectId && oracle.projectId !== 'all') {
  assert.equal(
    oracle.attribution,
    'explicit-cube',
    'Regenerate the raw oracle with explicit tenant+cube filtering; legacy tenant-only counts are not project evidence',
  )
  assert.ok(typeof oracle.cubeId === 'string' && oracle.cubeId)
}
const base = new URL(process.env.USAGE_BFF!)
assert.ok(['http:', 'https:'].includes(base.protocol) && !base.username && !base.password)
async function query(startDate: string, endDate: string) {
  const response = await fetch(new URL('/api/dashboard/usage/query', base), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ projectId: oracle.projectId, startDate, endDate }),
    signal: AbortSignal.timeout(10000),
  })
  assert.equal(response.status, 200)
  const body = (await response.json()) as Envelope<UsageResult>
  assert.equal(body.code, 0)
  return body.data.dailyStats
}
const days = await query(oracle.startDate, oracle.endDate)
assert.equal(days.length, Object.keys(oracle.days).length)
let total = 0,
  legacyDisplayed = 0
for (const day of days) {
  const expected = oracle.days[day.date]
  assert.ok(expected, 'Unexpected UTC day')
  assert.deepEqual(day.callsByEvent, expected)
  const count = Object.values(expected).reduce((a, b) => a + b, 0)
  assert.equal(day.totalCalls, count)
  assert.equal(day.addMessage.calls, expected.addMessage ?? 0)
  assert.equal(day.searchMemory.calls, expected.searchMemory ?? 0)
  assert.equal(day.rerank?.calls ?? 0, expected.rerank ?? 0)
  for (const group of ['addMessage', 'searchMemory', 'rerank', 'chat', 'extractMemory'] as const) {
    if (day[group]) assert.equal(day[group].totalTokens, 0)
  }
  if (count === 0) assert.ok(!day.chat && !day.extractMemory && !day.rerank, 'Phantom activity objects')
  total += count
  legacyDisplayed += day.addMessage.calls + day.searchMemory.calls
}
assert.equal(total, oracle.total)
assert.ok(total > 0, 'Real nonempty source required')
const empty = await query(oracle.emptyDate, oracle.emptyDate)
assert.equal(oracle.emptyRawCount, 0)
assert.equal(empty.length, 1)
assert.equal(empty[0].totalCalls, 0)
assert.deepEqual(empty[0].callsByEvent, {})
assert.ok(!empty[0].chat && !empty[0].extractMemory && !empty[0].rerank)
const report = {
  status: 'PASS',
  scope: 'actual raw JSONL versus deployed BFF; no browser assertion',
  total,
  byEvent: oracle.byEvent,
  days: days.length,
  emptyDay: 'PASS',
  memoryWrites: 0,
  unchangedFrontendFormula: legacyDisplayed,
  frontendTotalGap: total - legacyDisplayed,
}
if (process.env.USAGE_REPORT) await writeFile(process.env.USAGE_REPORT, JSON.stringify(report, null, 2))
console.log(JSON.stringify(report))
