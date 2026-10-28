import test from 'node:test'
import assert from 'node:assert/strict'
import { AuditService } from './services/audit.ts'
import { UsageService } from './services/usage.ts'
import { normalizeAuditRecord } from './adapters/audit-mapper.ts'

test('key usage: same-cube keys, revoked history, failures and unknown legacy remain distinct', async () => {
  const a = 'a'.repeat(24),
    b = 'b'.repeat(24)
  const events = [
    { api_key_id: a },
    { api_key_id: a, ok: false },
    { api_key_id: b },
    { input: { api_key_id: a } }, // Client payload cannot establish key identity.
    { api_key_id: 'bad' },
    { api_key_id: a, cube_id: 'foreign' },
    { api_key_id: a, tenant: 'foreign' },
    { api_key_id: a, cube_id: undefined }, // Known key cannot establish missing cube ownership.
  ].map((r, i) =>
    normalizeAuditRecord(
      { ts: '2026-10-21T08:00:00Z', tenant: 'owner', cube_id: 'cube', tool: 'get_user_profile', ok: true, ...r },
      { name: 'calls-keys.jsonl', offset: i, line: JSON.stringify(r) },
    ),
  )
  const project = { tenant: 'owner', cubeId: 'cube' }
  const audit = new AuditService({ rows: async () => events, badLines: () => 0 }, { all: () => [project], get: () => project })
  const service = new UsageService(audit)
  const q = { projectId: 'p', startDate: '2026-10-21', endDate: '2026-10-21' }
  for (const [key, count] of [
    [a, 2],
    [b, 1],
    ['c'.repeat(24), 0],
  ] as const) {
    const result = await service.query({ ...q, apiKeyId: key })
    assert.equal(result.dailyStats[0].totalCalls, count)
    assert.equal(result.dailyStats[0].callsByEvent.getMemory ?? 0, count)
    assert.equal(result.auditScope.unattributedKeyRecords, 2)
    assert.equal(result.auditScope.unattributedRecords, 1)
  }
  assert.equal((await service.query(q)).dailyStats[0].totalCalls, 5)
  assert.equal((await service.query({ ...q, projectId: 'all', apiKeyId: a })).dailyStats[0].totalCalls, 2)
  const empty = await service.query({ ...q, apiKeyId: a, startDate: '2026-10-22', endDate: '2026-10-22' })
  assert.equal(empty.dailyStats[0].totalCalls, 0)
  assert.equal(empty.auditScope.unattributedKeyRecords, 0)
})
