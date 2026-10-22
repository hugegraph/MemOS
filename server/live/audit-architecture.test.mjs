import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, writeFile, appendFile, rm, rename } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { AuditRepository } from './repositories/audit.mjs'
import { AuditService } from './services/audit.mjs'
import { UsageService } from './services/usage.mjs'
import { normalizeAuditRecord, toLogDetail } from './adapters/audit-mapper.mjs'

const event = (i = 0, extra = {}) => ({
  ts: '2026-10-01T12:00:00+0800',
  tenant: 'audit-A',
  cube_id: 'cube-A',
  tool: 'search_memory',
  ms: i,
  ok: i % 2 === 0,
  input: { query: `marker-${i}` },
  output: { found: i },
  ...extra,
})
async function setup(t, limits) {
  const dir = await mkdtemp(join(tmpdir(), 'audit-layer-'))
  t.after(() => rm(dir, { recursive: true, force: true }))
  const repository = new AuditRepository({ auditDir: dir }, limits)
  const workspaces = {
    get: (id) => ({ tenant: `audit-${id}`, cubeId: `cube-${id}` }),
    all: () => [
      { tenant: 'audit-A', cubeId: 'cube-A' },
      { tenant: 'audit-B', cubeId: 'cube-B' },
    ],
  }
  const audit = new AuditService(repository, workspaces)
  return { dir, file: join(dir, 'calls-test.jsonl'), repository, audit, usage: new UsageService(audit) }
}
const lines = (rows) => rows.map(JSON.stringify).join('\n') + '\n'
test('audit mapper: timezone, legacy JSON bodies, missing values and unknown tools are explicit', () => {
  const row = normalizeAuditRecord(event(3, { input: '{"user_id":"u"}', output: 'plain text' }), {
    name: 'calls-x.jsonl',
    offset: 0,
    line: 'fixture',
  })
  const detail = toLogDetail(row)
  assert.equal(detail.createTime, '2026-10-01T04:00:00.000Z')
  assert.deepEqual(JSON.parse(detail.requestBody), { user_id: 'u' })
  assert.equal(JSON.parse(detail.responseBody), 'plain text')
  assert.equal(detail.userId, 'u')
  assert.equal(detail.eventType, 'searchMemory')
  assert.throws(() => normalizeAuditRecord(event(3, { tool: 'future_tool' }), {}), /事件/)
  const legacy = toLogDetail(
    normalizeAuditRecord({ ts: event().ts, tenant: 'audit-A', tool: 'search_memory' }, { name: 'calls-x.jsonl', offset: 1, line: '{}' }),
  )
  assert.equal(legacy.requestBody, 'null')
  assert.equal(legacy.responseBody, 'null')
  assert.equal(legacy.eventType, 'searchMemory')
  assert.equal(legacy.duration, 0)
  assert.throws(() => normalizeAuditRecord({ ts: 'invalid', tenant: 'audit-A' }, {}))
  assert.throws(() => normalizeAuditRecord({ ts: event().ts }, {}))
})
test('audit repository: concurrent refresh, partial append, stable IDs and untrusted extra prefixes', async (t) => {
  const h = await setup(t)
  await writeFile(h.file, lines([event()]) + '{"ts":')
  await writeFile(join(h.dir, 'requests-test.jsonl'), lines([event(99)]))
  const [a, b] = await Promise.all([h.repository.rows(), h.repository.rows()])
  assert.equal(a.length, 1)
  assert.equal(b[0].id, a[0].id)
  await appendFile(h.file, JSON.stringify(event().ts) + ',"tenant":"audit-A","tool":"add_message"}\n')
  const after = await h.repository.rows()
  assert.equal(after.length, 2)
  assert.ok(after.some((r) => r.id === a[0].id))
})
test('audit repository: same-inode rewrite plus growth does not retain old rows', async (t) => {
  const h = await setup(t)
  await writeFile(h.file, lines([event(1)]))
  await h.repository.rows()
  await writeFile(h.file, lines([event(2), event(3)]))
  assert.deepEqual((await h.repository.rows()).map((r) => r.raw.ms).sort(), [2, 3])
})
test('audit repository: completed oversized lines reject the query, and repaired files recover', async (t) => {
  const h = await setup(t, { maxLineBytes: 200 })
  await writeFile(h.file, lines([event(1, { output: 'x'.repeat(201) })]))
  await assert.rejects(h.repository.rows(), (error) => error.status === 503)
  await writeFile(h.file, lines([event()]))
  assert.equal((await h.repository.rows()).length, 1)
})
test('audit repository: truncate, rotation, bad rows and removal are reflected in one snapshot', async (t) => {
  const h = await setup(t)
  await writeFile(h.file, lines([event(1), event(2)]))
  await h.repository.rows()
  await writeFile(h.file, lines([event(3)]) + 'BAD\n')
  assert.equal((await h.repository.rows()).length, 1)
  assert.equal(h.repository.badLines(), 1)
  await rename(h.file, join(h.dir, 'calls-old.jsonl'))
  await writeFile(h.file, lines([event(4)]))
  assert.equal((await h.repository.rows()).length, 2)
  await rm(join(h.dir, 'calls-old.jsonl'))
  assert.equal((await h.repository.rows()).length, 1)
})
test('logs and usage: share the same repository, respect audit identity and count failed calls', async (t) => {
  const h = await setup(t)
  await writeFile(
    h.file,
    lines([
      event(0),
      event(1),
      event(2, { tenant: 'audit-B', cube_id: 'cube-B' }),
      event(3, { tool: 'add_message', ts: '2026-10-02T00:00:00Z' }),
    ]),
  )
  assert.equal((await h.audit.list({ projectId: 'A', pageSize: 1 })).total, 3)
  const list = await h.audit.list({ projectId: 'A', status: 'FAILED', eventType: 'searchMemory' })
  assert.equal(list.total, 1)
  assert.equal((await h.audit.detail(list.logs[0].id)).duration, 1)
  const result = await h.usage.query({ projectId: 'A', startDate: '2026-09-30', endDate: '2026-10-02' })
  assert.deepEqual(
    result.dailyStats.map((d) => [d.searchMemory.calls, d.addMessage.calls]),
    [
      [0, 0],
      [2, 0],
      [0, 1],
    ],
  )
  assert.ok(result.dailyStats.every((d) => d.searchMemory.totalTokens === 0))
  await appendFile(h.file, lines([event(5)]))
  assert.equal(
    (await h.usage.query({ projectId: 'A', startDate: '2026-10-01', endDate: '2026-10-01' })).dailyStats[0].searchMemory.calls,
    3,
  )
})
test('usage: impossible calendar dates and missing audit source never yield fake zero usage', async (t) => {
  const h = await setup(t)
  await assert.rejects(h.usage.query({ startDate: '2026-02-30', endDate: '2026-03-01' }), (error) => error.status === 400)
  const unavailable = new UsageService(new AuditService(new AuditRepository({ auditDir: '' }), { all: () => [] }))
  await assert.rejects(unavailable.query({}), (error) => error.status === 503)
})
test('audit scope: legacy records cannot pretend to distinguish two cubes sharing one tenant', async (t) => {
  const h = await setup(t)
  await writeFile(h.file, lines([event(0, { cube_id: undefined })]))
  const projects = ['x', 'y'].map((id) => ({ id, cubeId: id, tenant: 'audit-A' }))
  const service = new AuditService(h.repository, { all: () => projects, get: (id) => projects.find((p) => p.id === id) })
  const scoped = await service.list({ projectId: 'x' })
  assert.equal(scoped.total, 0)
  assert.equal(scoped.auditScope.unattributedRecords, 1)
  assert.equal((await service.list({ projectId: 'all' })).total, 1)
  await writeFile(h.file, lines([event(0, { cube_id: 'x' }), event(1, { cube_id: 'y' })]))
  assert.equal((await service.list({ projectId: 'x' })).total, 1)
})
