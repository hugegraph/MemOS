import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { config } from './core.mjs'
import { MemOSClient } from './adapters/memos-client.mjs'
import { WorkspaceRepository } from './repositories/workspaces.mjs'
import { WorkspaceService } from './services/workspaces.mjs'
import { MemoryService } from './services/memories.mjs'
import { selectMemoryTab, toDashboardMemory } from './adapters/memory-mapper.mjs'
import { buildApp } from './server.mjs'

const record = (id, status = 'activated') => ({
  id,
  memory: `记忆 ${id}`,
  metadata: { status, user_id: 'user-x', created_at: '2026-10-01T08:00:00+08:00', embedding: [1, 2], sources: ['private'] },
})
const project = { id: 'project-x', name: 'Workspace', userId: 'user-x', cubeId: 'cube-x', tenant: 'audit-user', status: 'ENABLED' }
const binding = { projectId: 'project-x', principal: { userId: 'user-x', cubeId: 'cube-x' }, auditTenant: 'audit-user' }
async function setup(t, projects = [project]) {
  const dir = await mkdtemp(join(tmpdir(), 'memos-architecture-'))
  t.after(() => rm(dir, { recursive: true, force: true }))
  const settings = config({ DASHBOARD_DATA_DIR: dir, DASHBOARD_WORKSPACES: JSON.stringify(projects), MEMOS_CUBE_OWNER: 'owner-x' })
  const repository = new WorkspaceRepository(settings)
  const workspaces = new WorkspaceService(repository, { createCube: async (body) => ({ data: { cube_id: body.cube_id } }) }, settings.owner)
  return { dir, settings, repository, workspaces }
}

test('architecture: workspace resolves distinct project/user/cube/audit identities without guessing', async (t) => {
  const { workspaces } = await setup(t)
  assert.deepEqual(workspaces.resolveWorkspace('project-x'), binding)
  const copy = workspaces.get('project-x')
  copy.cubeId = 'foreign'
  assert.equal(workspaces.resolveWorkspace('project-x').principal.cubeId, 'cube-x')
  assert.throws(
    () => workspaces.resolveWorkspace('missing'),
    (error) => error.status === 404,
  )
})
test('architecture: legacy project file is readable and identity cannot be changed by public update', async (t) => {
  const { workspaces, settings } = await setup(t)
  workspaces.update({ id: 'project-x', name: 'Renamed', cubeId: 'foreign', userId: 'foreign' })
  const reread = new WorkspaceRepository(settings)
  assert.equal(reread.get('project-x').name, 'Renamed')
  assert.equal(reread.get('project-x').cubeId, 'cube-x')
})
test('architecture: repository validates before replacing state or file', async (t) => {
  const { repository, dir } = await setup(t)
  const before = await readFile(join(dir, 'projects.json'), 'utf8')
  assert.throws(() => repository.replace([project, project]))
  assert.throws(() => repository.replace([{ ...project, cubeId: 123 }]))
  assert.equal(await readFile(join(dir, 'projects.json'), 'utf8'), before)
  assert.equal(repository.all().length, 1)
})
test('architecture: failed upstream cube creation leaves no invented project', async (t) => {
  const { repository } = await setup(t)
  const workspaces = new WorkspaceService(
    repository,
    {
      createCube: async () => {
        throw new Error('offline')
      },
    },
    'owner-x',
  )
  await assert.rejects(workspaces.create({ name: 'new' }), /offline/)
  assert.equal(repository.all().length, 1)
})
test('architecture: MemOSClient uses upstream field names and explicit binding', async () => {
  const calls = []
  const client = new MemOSClient(config({ DASHBOARD_PROMPT_TOKEN: 'fixture-private-token' }), {
    fetch: async (url, options) => {
      calls.push({ url, options, body: options.body && JSON.parse(options.body) })
      return Response.json({ code: 200, data: url.endsWith('/soft-delete') ? { deleted: 1, soft_deleted: true } : { text_mem: [] } })
    },
  })
  await client.getMemory(binding, 'text_mem')
  await client.deleteMemory(['memory-x'], binding)
  assert.equal(calls[0].body.user_id, 'user-x')
  assert.equal(calls[0].body.mem_cube_id, 'cube-x')
  assert.equal(calls[0].body.page, null)
  assert.equal(calls[1].body.mem_cube_id, 'cube-x')
  assert.equal(calls[1].body.user_id, 'user-x')
  assert.equal(calls[1].options.headers['X-Dashboard-Prompt-Token'], 'fixture-private-token')
  assert.ok(calls[1].url.endsWith('/product/dashboard-memory/soft-delete'))
  assert.equal(calls[0].options.redirect, 'error')
})
for (const [name, reply, status] of [
  ['business error', () => Response.json({ code: 500, message: 'private' }), 502],
  ['invalid JSON', () => new Response('private'), 502],
  ['failure body', () => Response.json({ code: 200, data: { status: 'failure' } }), 502],
  ['null envelope', () => Response.json(null), 502],
  [
    'timeout',
    () => {
      throw Object.assign(new Error('private'), { name: 'TimeoutError' })
    },
    504,
  ],
])
  test(`architecture: client normalizes ${name} without leaking upstream contents`, async () => {
    const client = new MemOSClient(config(), { fetch: async () => reply() })
    await assert.rejects(client.health(), (error) => error.status === status && !error.message.includes('private'))
  })
test('architecture: client enforces response bound without silent truncation', async () => {
  const client = new MemOSClient(config(), { maxResponseBytes: 8, fetch: async () => new Response('{"large":"payload"}') })
  await assert.rejects(client.health(), (error) => error.status === 502)
})
test('architecture: mapper keeps only active unique records from the bound cube', () => {
  const rows = [record('a'), record('a'), record('old', 'archived'), record('gone', 'deleted')]
  const response = {
    text_mem: [
      { cube_id: 'foreign', memories: [record('foreign')] },
      { cube_id: 'cube-x', memories: rows },
    ],
  }
  const before = structuredClone(response)
  assert.deepEqual(
    selectMemoryTab(response, 'text_mem', binding).map((r) => r.id),
    ['a'],
  )
  assert.deepEqual(response, before)
  assert.deepEqual(selectMemoryTab(response, 'profile_mem', binding), []) // No explicit profile marker in this fixture.
  assert.throws(
    () => selectMemoryTab({}, 'text_mem', binding),
    (error) => error.status === 502,
  )
})
test('architecture: Dashboard projection reuses contract names and strips native metadata', () => {
  const row = record('a')
  row.memory = '中文🙂'.repeat(10000)
  const full = toDashboardMemory(row)
  assert.equal(full.memoryContent, row.memory)
  assert.equal(full.createTime, '2026-10-01T00:00:00.000Z')
  assert.equal(full.externalUserId, 'user-x')
  assert.ok(!('metadata' in full))
  assert.ok(toDashboardMemory(row, { maxContentLength: 30 }).memoryContent.length <= 31)
})
test('architecture: MemoryService validates all IDs before any destructive request', async (t) => {
  const { workspaces } = await setup(t)
  const writes = []
  const service = new MemoryService(workspaces, {
    getMemory: async () => ({ data: { text_mem: [{ cube_id: 'cube-x', memories: [record('a')] }] } }),
    deleteMemory: async (...args) => {
      writes.push(args)
    },
  })
  await assert.rejects(service.delete(['a', 'foreign'], 'text_mem'), (error) => error.status === 404)
  assert.deepEqual(writes, [])
  await service.delete(['a', 'a'], 'text_mem')
  assert.deepEqual(writes, [[['a'], binding]])
})
test('architecture: composition root accepts one injected client for health and memory', async (t) => {
  const { settings } = await setup(t)
  const calls = []
  const app = buildApp(settings, {
    client: {
      health: async () => {
        calls.push('health')
        return { status: 'healthy' }
      },
      getMemory: async (b) => {
        calls.push(b.principal.cubeId)
        return { data: { text_mem: [{ cube_id: 'cube-x', memories: [record('a')] }] } }
      },
    },
  })
  await new Promise((resolve) => app.listen(0, '127.0.0.1', resolve))
  t.after(() => {
    app.closeAllConnections()
    return new Promise((resolve) => app.close(resolve))
  })
  const url = `http://127.0.0.1:${app.address().port}`
  assert.equal((await (await fetch(`${url}/api/meta`)).json()).data.health.status, 'healthy')
  const response = await fetch(`${url}/api/dashboard/project/memory/list`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ projectId: 'project-x', memoryDetailType: 'text_mem' }),
  })
  assert.equal((await response.json()).data.total, 1)
  assert.deepEqual(calls, ['health', 'cube-x'])
  // Constructor must not overwrite persisted data with new environment defaults.
  await writeFile(join(settings.dataDir, 'projects.json'), JSON.stringify([project]))
  assert.equal(new WorkspaceRepository({ ...settings, projects: [] }).all().length, 1)
})

test('architecture: batch deletion reads a cube only once and still verifies the entire batch', async (t) => {
  const { workspaces } = await setup(t)
  let reads = 0
  const writes = []
  const service = new MemoryService(workspaces, {
    getMemory: async () => {
      reads++
      return { data: { text_mem: [{ cube_id: 'cube-x', memories: ['a', 'b', 'c'].map((id) => record(id)) }] } }
    },
    deleteMemory: async (...args) => {
      writes.push(args)
    },
  })
  assert.deepEqual(await service.delete(['a', 'b', 'c'], 'text_mem'), { deleted: 3 })
  assert.equal(reads, 1)
  assert.deepEqual(writes, [[['a', 'b', 'c'], binding]])
})
