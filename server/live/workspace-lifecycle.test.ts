import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, rm, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { WorkspaceRepository } from './repositories/workspaces.ts'
import { WorkspaceService } from './services/workspaces.ts'
import { selectMemoryTab, toDashboardMemory, explicitMemoryType } from './adapters/memory-mapper.ts'
import { ApiError } from './errors.ts'
import type { TestContext } from 'node:test'
import type { CubeCreator } from './types.ts'

async function setup(t: TestContext, client: CubeCreator = { createCube: async (b) => ({ data: { cube_id: b.cube_id } }) }) {
  const dir = await mkdtemp(join(tmpdir(), 'workspace-life-'))
  t.after(() => rm(dir, { recursive: true, force: true }))
  const settings = {
    dataDir: dir,
    projects: ['A', 'B'].map((id) => ({
      id,
      name: id,
      cubeId: `cube-${id}`,
      userId: 'owner',
      tenant: 'owner',
      status: id === 'A' ? 'ENABLED' : 'DISABLED',
    })),
  }
  const repository = new WorkspaceRepository(settings)
  return { settings, service: new WorkspaceService(repository, client, 'owner'), repository }
}
test('workspace: selection survives restart, active revoke selects a survivor, final revoke leaves no fake project', async (t) => {
  const { service, settings } = await setup(t)
  service.update({ id: 'B', status: 'ENABLED' })
  assert.equal(new WorkspaceRepository(settings).get('B').status, 'ENABLED')
  service.revoke('B')
  assert.equal(service.get('A').status, 'ENABLED')
  assert.equal(new WorkspaceRepository(settings).get('A').status, 'ENABLED')
  service.revoke('A')
  assert.deepEqual(service.dict(), [])
  assert.throws(
    () => service.resolveWorkspace('B'),
    (e) => e instanceof ApiError && e.status === 404,
  )
})
test('workspace: invalid status and disabling the last selection do not partially persist changes', async (t) => {
  const { service, settings } = await setup(t)
  const path = join(settings.dataDir, 'projects.json'),
    before = await readFile(path, 'utf8')
  assert.throws(
    () => service.update({ id: 'A', name: 'changed', status: 'unknown' }),
    (e) => e instanceof ApiError && e.status === 400,
  )
  assert.equal(await readFile(path, 'utf8'), before)
  service.revoke('B')
  assert.throws(
    () => service.update({ id: 'A', status: 'DISABLED' }),
    (e) => e instanceof ApiError && e.status === 400,
  )
  assert.equal(service.get('A').status, 'ENABLED')
})
test('workspace: pending creation reserves its name against concurrent rename', async (t) => {
  let complete!: () => void
  const { service } = await setup(t, {
    createCube: (body) =>
      new Promise((resolve) => {
        complete = () => resolve({ data: { cube_id: body.cube_id } })
      }),
  })
  const creation = service.create({ name: 'reserved' })
  assert.throws(
    () => service.update({ id: 'B', name: 'reserved' }),
    (e) => e instanceof ApiError && e.status === 409,
  )
  await assert.rejects(service.create({ name: 'second' }), (e) => e instanceof ApiError && e.status === 409)
  complete()
  const created = await creation
  assert.equal(created.name, 'reserved')
  assert.equal(service.resolveWorkspace(created.id).principal.userId, 'owner')
  assert.equal(service.resolveWorkspace(created.id).principal.cubeId, created.id)
})

test('workspace: legacy optional status does not prevent renaming a single imported workspace', async (t) => {
  const { service, repository } = await setup(t)
  const project = service.get('A')
  delete project.status
  repository.replace([project])
  assert.equal(service.update({ id: 'A', name: 'renamed' }).name, 'renamed')
  assert.equal(service.get('A').status, 'ENABLED')
})
test('memory tags: six views use four buckets; tagged subsets may overlap the text view', () => {
  const row = (id: string, type?: string, memory_type = 'WorkingMemory') => ({
    id,
    memory: id,
    metadata: { type, memory_type, status: 'activated' },
  })
  const response = {
    text_mem: [
      {
        cube_id: 'cube-A',
        memories: [row('plain'), row('event', 'event_mem'), row('profile', 'profile_mem'), row('long', undefined, 'LongTermMemory')],
      },
    ],
  }
  const binding = { principal: { cubeId: 'cube-A' } }
  assert.deepEqual(
    selectMemoryTab(response, 'text_mem', binding).map((r) => r.id),
    ['plain', 'event', 'profile', 'long'],
  )
  assert.deepEqual(
    selectMemoryTab(response, 'event_mem', binding).map((r) => r.id),
    ['event'],
  )
  assert.deepEqual(
    selectMemoryTab(response, 'profile_mem', binding).map((r) => r.id),
    ['profile'],
  )
})
test('profile projection: carry explicit optional identifiers, never invent template bindings', () => {
  const plain = { id: 'p', memory: '{"facts":{"role":{"value":"tester"}}}', metadata: { type: 'profile_mem' } }
  assert.ok(!('profileTemplateId' in toDashboardMemory(plain)))
  const marked = {
    ...plain,
    metadata: { ...plain.metadata, profile_template_id: 'existing-template', profile_instance_id: 'existing-instance' },
  }
  assert.equal(toDashboardMemory(marked).profileTemplateId, 'existing-template')
  assert.equal(toDashboardMemory(marked).profileInstanceId, 'existing-instance')
})

test('memory tags: REST extension works with native fact type, and ambiguous markers fail closed', () => {
  assert.equal(explicitMemoryType({ type: 'fact', dashboard_type: 'event_mem' }), 'event_mem')
  assert.equal(explicitMemoryType({ type: 'profile_mem', dashboard_type: 'profile_mem' }), 'profile_mem')
  assert.throws(
    () => explicitMemoryType({ type: 'event_mem', dashboard_type: 'profile_mem' }),
    (e) => e instanceof ApiError && e.status === 502,
  )
  assert.throws(
    () => explicitMemoryType({ dashboard_type: 'unknown' }),
    (e) => e instanceof ApiError && e.status === 502,
  )
  const row = { id: 'rest-profile', memory: 'profile content', metadata: { type: 'fact', dashboard_type: 'profile_mem' } }
  const data = { text_mem: [{ cube_id: 'cube-A', memories: [row] }] },
    binding = { principal: { cubeId: 'cube-A' } }
  assert.deepEqual(
    selectMemoryTab(data, 'profile_mem', binding).map((r) => r.id),
    ['rest-profile'],
  )
  assert.deepEqual(selectMemoryTab(data, 'event_mem', binding), [])
})
