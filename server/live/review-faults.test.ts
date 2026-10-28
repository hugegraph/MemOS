import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { TemplateRepository } from './repositories/templates.ts'
import { WorkspaceRepository } from './repositories/workspaces.ts'
import { WorkspaceService } from './services/workspaces.ts'
import { KeyService } from './services/keys.ts'
import { GatewayConfigAdapter } from './adapters/gateway-config.ts'
import { AuditRepository } from './repositories/audit.ts'
import { AuditService } from './services/audit.ts'
import { execFileSync } from 'node:child_process'
import { workspaceBinding } from './domain/workspace.ts'

test('template failure leaves memory and durable state unchanged; malformed store fails closed', (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'review-template-'))
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  const file = join(dir, 'templates.json')
  const row = { templateId: 't', projectId: 'p', templateName: 'original', group: { field: { value: 'x', algorithm_updatable: false } } }
  writeFileSync(file, JSON.stringify([row]))
  const store = new TemplateRepository(file, () => {
    throw new Error('ENOSPC')
  })
  assert.throws(() => store.replace([{ ...row, templateName: 'changed' }]), /ENOSPC/)
  assert.deepEqual(store.all(), [row])
  assert.deepEqual(new TemplateRepository(file).all(), [row])
  writeFileSync(file, '{}')
  assert.throws(() => new TemplateRepository(file))
  writeFileSync(file, JSON.stringify([{ ...row, group: 'corrupt' }]))
  assert.throws(() => new TemplateRepository(file))
})

test('stale template repository cannot overwrite a successful writer', (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'review-template-cas-'))
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  const file = join(dir, 'templates.json')
  const a = new TemplateRepository(file),
    b = new TemplateRepository(file)
  const row = { templateId: 't', projectId: 'p', templateName: 'winner' }
  a.replace([row])
  assert.throws(() => b.replace([{ ...row, templateName: 'stale' }]), /其他实例/)
  assert.equal(new TemplateRepository(file).all()[0].templateName, 'winner')
})

test('upstream-confirmed workspace can recover after local persistence failure without duplicate creation', async (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'review-workspace-'))
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  const repository = new WorkspaceRepository({ dataDir: dir, projects: [] })
  let creates = 0,
    fail = true
  const client = {
    createCube: async (body: { cube_id: string }) => {
      creates++
      return { data: { cube_id: body.cube_id } }
    },
  }
  const storage = {
    all: () => repository.all(),
    get: (id: unknown) => repository.get(id),
    replace: (value: unknown) => {
      if (fail) throw new Error('ENOSPC')
      repository.replace(value)
    },
  }
  const journal = join(dir, 'recovery.json')
  const service = new WorkspaceService(storage, client, 'owner', journal)
  await assert.rejects(service.create({ name: 'new' }), /ENOSPC/)
  fail = false
  const restarted = new WorkspaceService(storage, client, 'owner', journal)
  const recovered = await restarted.create({ name: 'new' })
  assert.equal(repository.all().length, 1)
  assert.equal(creates, 1)
  assert.equal(recovered.name, 'new')
})

test('failed metadata persistence never activates a key; failed gateway save compensates metadata', (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'review-key-'))
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  const file = join(dir, 'gateway.env')
  writeFileSync(file, "MEMOS_TENANTS='{}'\n")
  const adapter = new GatewayConfigAdapter(file)
  const project = { id: 'p', name: 'p', userId: 'owner', cubeId: 'cube', tenant: 'owner' }
  const workspaces = {
    all: () => [project],
    resolveWorkspace: () => ({ projectId: 'p', auditTenant: 'owner', principal: { userId: 'owner', cubeId: 'cube' } }),
  }
  const keys = new KeyService(workspaces, adapter, dir)
  keys.saveMetadata = () => {
    throw new Error('ENOSPC')
  }
  assert.throws(() => keys.create({ projectId: 'p', keyName: 'x' }), /ENOSPC/)
  assert.deepEqual(adapter.load().entries, {})
  const next = new KeyService(
    workspaces,
    {
      load: () => adapter.load(),
      save: () => {
        throw new Error('conflict')
      },
    },
    dir,
  )
  assert.throws(() => next.create({ projectId: 'p', keyName: 'x' }), /conflict/)
  assert.deepEqual(next.metadata, {})
})

test('rejected audit rows are visible through API diagnostics without foreign tenant counts', async (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'review-audit-'))
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  const ts = '2026-10-26T00:00:00Z'
  writeFileSync(
    join(dir, 'calls-x.jsonl'),
    [
      JSON.stringify({ ts, tenant: 'owner', cube_id: 'cube', tool: 'search_memory', ok: true }),
      JSON.stringify({ ts, tenant: 'owner', tool: 'future_tool' }),
      JSON.stringify({ ts, tool: 'search_memory' }),
      JSON.stringify({ ts, tenant: 'foreign', tool: 'future_tool' }),
      'BAD',
    ].join('\n') + '\n',
  )
  const repo = new AuditRepository({ auditDir: dir })
  const service = new AuditService(repo, {
    all: () => [{ tenant: 'owner', cubeId: 'cube' }],
    get: () => ({ tenant: 'owner', cubeId: 'cube' }),
  })
  const response = await service.list({ projectId: 'p' })
  assert.equal(response.total, 1)
  assert.deepEqual(response.auditScope.diagnostics?.reasons, { unknown_event: 1, missing_tenant: 1, invalid_json: 1 })
  assert.equal(response.auditScope.diagnostics?.rejectedRecords, 3)
  assert.equal(response.auditScope.diagnostics?.incomplete, true)
})

test('actual Python audit decorator output remains visible under distinct project/user/cube IDs', async (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'review-cross-identity-'))
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  const raw = execFileSync(
    'python',
    [
      '-c',
      "import sys,json; from types import SimpleNamespace as S; sys.path.insert(0,'deploy/dashboard-bff/gateway-keys'); from scoped_audit import ScopedAuditSink; rows=[]; sink=ScopedAuditSink(S(write=rows.append),S(current=lambda:S(user_id='owner',cube_id='cube'))); sink.write({'ts':'2026-10-26T00:00:00Z','tool':'search_memory','ok':True,'tenant':'untrusted','cube_id':'untrusted'}); print(json.dumps(rows[0]))",
    ],
    { encoding: 'utf8', windowsHide: true },
  )
  writeFileSync(join(dir, 'calls-python.jsonl'), raw.trim() + '\n')
  const binding = workspaceBinding({ id: 'project', userId: 'owner', cubeId: 'cube', tenant: 'owner' })
  const scoped = { tenant: binding.auditTenant, cubeId: binding.principal.cubeId }
  const service = new AuditService(new AuditRepository({ auditDir: dir }), { all: () => [scoped], get: () => scoped })
  const page = await service.list({ projectId: 'project' })
  assert.equal(page.total, 1)
  assert.equal(page.logs[0].cubeId, 'cube')
  assert.equal(page.auditScope.diagnostics?.incomplete, false)
})
