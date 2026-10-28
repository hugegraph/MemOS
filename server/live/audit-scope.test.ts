import assert from 'node:assert/strict'
import test from 'node:test'
import { AuditService } from './services/audit.ts'
import { UsageService } from './services/usage.ts'
import { normalizeAuditRecord } from './adapters/audit-mapper.ts'
import { ApiError } from './errors.ts'

function fixture() {
  let projects = [{ id: 'A', tenant: 'owner', cubeId: 'cube-A' }]
  const raw = [
    { tenant: 'owner', input: { cube_id: 'cube-A' } }, // Payload does not establish identity.
    { tenant: 'owner', cube_id: 'cube-A' },
    { tenant: 'owner', cube_id: 'cube-B', ok: false },
    { tenant: 'owner', cube_id: 'revoked-cube' },
    { tenant: 'foreign', cube_id: 'cube-A' },
    { tenant: 'owner', cube_id: '  ' },
  ].map((r, i) =>
    normalizeAuditRecord(
      { ts: '2026-10-21T08:00:00Z', tool: 'get_user_profile', ok: true, ...r },
      { name: 'calls-scope.jsonl', offset: i, line: JSON.stringify(r) },
    ),
  )
  const service = new AuditService(
    { rows: async () => raw, badLines: () => 0 },
    {
      all: () => projects,
      get: (id) => projects.find((p) => p.id === id)!,
    },
  )
  return {
    service,
    raw,
    setProjects: (value: typeof projects) => {
      projects = value
    },
  }
}

test('scope: even a sole current cube cannot inherit tenant history or another cube', async () => {
  const { service, raw } = fixture()
  const page = await service.list({ projectId: 'A' })
  assert.deepEqual(
    page.logs.map((r) => r.id),
    [raw[1].id],
  )
  assert.equal(page.auditScope.unattributedRecords, 2)
  assert.equal(page.logs[0].cubeId, 'cube-A')
})

test('scope: shared tenant does not fail all reads; counts and detail stay within configured cubes', async () => {
  const { service, raw, setProjects } = fixture()
  setProjects([
    { id: 'A', tenant: 'owner', cubeId: 'cube-A' },
    { id: 'B', tenant: 'owner', cubeId: 'cube-B' },
  ])
  for (const [id, index] of [
    ['A', 1],
    ['B', 2],
  ] as const) {
    const page = await service.list({ projectId: id })
    assert.deepEqual(
      page.logs.map((r) => r.id),
      [raw[index].id],
    )
    const usage = await new UsageService(service).query({ projectId: id, startDate: '2026-10-21', endDate: '2026-10-21' })
    assert.equal(usage.dailyStats[0].totalCalls, 1)
    assert.equal(usage.auditScope.unattributedRecords, 2)
  }
  const all = await service.list({ projectId: 'all' })
  assert.equal(all.total, 4)
  assert.equal(all.logs.filter((r) => r.attribution === 'unknown').length, 2)
  assert.equal((await service.detail(raw[0].id)).attribution, 'unknown')
  await assert.rejects(service.detail(raw[3].id), (e) => e instanceof ApiError && e.status === 404)
  await assert.rejects(service.detail(raw[4].id), (e) => e instanceof ApiError && e.status === 404)
  setProjects([{ id: 'A', tenant: 'owner', cubeId: 'cube-A' }])
  await assert.rejects(service.detail(raw[2].id), (e) => e instanceof ApiError && e.status === 404)
})

test('scope: empty bounded window reports no missing historical records', async () => {
  const { service } = fixture()
  const page = await service.list({ projectId: 'A', startTime: '2026-10-22T00:00:00Z' })
  assert.equal(page.total, 0)
  assert.equal(page.auditScope.unattributedRecords, 0)
  const usage = await new UsageService(service).query({ projectId: 'A', startDate: '2026-10-22', endDate: '2026-10-22' })
  assert.equal(usage.dailyStats[0].totalCalls, 0)
  assert.equal(usage.auditScope.unattributedRecords, 0)
})
