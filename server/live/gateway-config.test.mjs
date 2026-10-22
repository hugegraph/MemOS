import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { config } from './core.mjs'
import { parseTenants, GatewayConfigAdapter } from './adapters/gateway-config.mjs'
import { WorkspaceRepository } from './repositories/workspaces.mjs'
import { WorkspaceService } from './services/workspaces.mjs'
import { KeyService } from './services/keys.mjs'
import { buildRoutes } from './app.mjs'

async function setup(t, content = "OTHER=preserve\r\nMEMOS_TENANTS='{}'\r\nTAIL=value\r\n") {
  const dir = await mkdtemp(join(tmpdir(), 'memos-keys-'))
  t.after(() => rm(dir, { recursive: true, force: true }))
  const env = join(dir, 'gateway.env')
  await writeFile(env, content)
  const settings = config({
    DASHBOARD_DATA_DIR: dir,
    DASHBOARD_GATEWAY_ENV: env,
    DASHBOARD_WORKSPACES: JSON.stringify(
      ['A', 'B'].map((id) => ({ id, name: id, userId: 'shared-user', cubeId: `cube-${id}`, tenant: 'shared-user' })),
    ),
  })
  const workspaces = new WorkspaceService(new WorkspaceRepository(settings), {}, '')
  const adapter = new GatewayConfigAdapter(env)
  return { settings, adapter, env, workspaces, keys: new KeyService(workspaces, adapter, dir) }
}
test('gateway: original compact syntax uses last colon; JSON supports distinct cube and default cube', () => {
  assert.deepEqual(parseTenants('token:with:colon:user'), { 'token:with:colon': { userId: 'user', cubeId: 'user' } })
  assert.deepEqual(parseTenants('{"a":"user","b":{"user_id":"user","cube_id":"cube"},"c":{"user_id":"user"}}'), {
    a: { userId: 'user', cubeId: 'user' },
    b: { userId: 'user', cubeId: 'cube' },
    c: { userId: 'user', cubeId: 'user' },
  })
})
test('gateway: invalid config errors are sanitized and never contain token values', () => {
  for (const value of ['{"sensitive-token":{"cube_id":"x"}}', '{"sensitive-token":123}', 'sensitive-token', '[]'])
    assert.throws(
      () => parseTenants(value),
      (error) => error.status === 503 && !error.message.includes('sensitive-token'),
    )
})
test('gateway: object/string forms coexist; writes preserve unrelated CRLF lines and reject stale snapshots', async (t) => {
  const h = await setup(
    t,
    'OTHER=preserve\r\nMEMOS_TENANTS=\'{"legacy":"user","external":{"user_id":"other","cube_id":"other-cube"}}\'\r\nTAIL=value\r\n',
  )
  const first = h.adapter.load()
  h.adapter.save(first, { ...first.entries, added: { user_id: 'shared-user', cube_id: 'cube-A' } })
  const text = await readFile(h.env, 'utf8')
  assert.ok(text.startsWith('OTHER=preserve\r\n'))
  assert.ok(text.endsWith('\r\nTAIL=value\r\n'))
  assert.deepEqual(h.adapter.load().entries.external, { user_id: 'other', cube_id: 'other-cube' })
  assert.equal(h.adapter.load().entries.legacy, 'user')
  assert.throws(
    () => h.adapter.save(first, {}),
    (error) => error.status === 409,
  )
  assert.equal(await readFile(h.env, 'utf8'), text)
})
test('gateway: same user with distinct cubes has separate key lists; create stores real cube', async (t) => {
  const h = await setup(t)
  const created = h.keys.create({ projectId: 'A', keyName: 'A key' })
  assert.deepEqual(h.adapter.load().entries[created.apiKey], { user_id: 'shared-user', cube_id: 'cube-A' })
  assert.equal(h.keys.list({ projectId: 'A' }).total, 1)
  assert.equal(h.keys.list({ projectId: 'B' }).total, 0)
  assert.equal(h.keys.copy(created.id), created.apiKey)
  const restarted = new KeyService(h.workspaces, h.adapter, h.settings.dataDir)
  assert.equal(restarted.list({ projectId: 'A' }).records[0].keyName, 'A key')
  restarted.revoke(created.id)
  assert.equal(h.adapter.load().entries[created.apiKey], undefined)
})
test('gateway: copy/revoke cannot access an unconfigured cube even when user matches', async (t) => {
  const h = await setup(t)
  const initial = h.adapter.load()
  h.adapter.save(initial, { foreign: { user_id: 'shared-user', cube_id: 'unconfigured' } })
  assert.equal(h.keys.list({ projectId: 'A' }).total, 0)
  const { createHash } = await import('node:crypto')
  const id = createHash('sha256').update('foreign').digest('hex').slice(0, 24)
  assert.throws(
    () => h.keys.copy(id),
    (error) => error.status === 404,
  )
  assert.throws(
    () => h.keys.revoke(id),
    (error) => error.status === 404,
  )
  assert.ok(h.adapter.load().entries.foreign)
})
test('gateway: management routes use the shared adapter and binding, not legacy string-only parsing', async (t) => {
  const h = await setup(t)
  const routes = buildRoutes(h.settings)
  const key = routes['POST /api/dashboard/keys/create']({ body: { projectId: 'A', keyName: 'A' } })
  assert.equal(routes['POST /api/dashboard/keys/list']({ body: { projectId: 'B' } }).total, 0)
  assert.deepEqual(h.adapter.load().tenants[key.apiKey], { userId: 'shared-user', cubeId: 'cube-A' })
})

test('gateway: new cube and its generated key retain different owner/cube identities', async (t) => {
  const h = await setup(t)
  const routes = buildRoutes(
    { ...h.settings, owner: 'shared-user' },
    { client: { createCube: async (body) => ({ data: { cube_id: body.cube_id } }) } },
  )
  const project = await routes['POST /api/dashboard/project/create']({ body: { name: 'Created workspace' } })
  const key = routes['POST /api/dashboard/keys/create']({ body: { projectId: project.id, keyName: 'Created key' } })
  assert.deepEqual(h.adapter.load().tenants[key.apiKey], { userId: 'shared-user', cubeId: project.id })
  assert.equal(routes['POST /api/dashboard/keys/list']({ body: { projectId: 'A' } }).total, 0)
})

test('gateway: dotenv quoting round trips escaped JSON, apostrophes and backslashes', async (t) => {
  const entries = { legacy: { user_id: "O'Brien", cube_id: 'folder\\cube' } }
  const h = await setup(t, `OTHER=yes\nMEMOS_TENANTS=${JSON.stringify(JSON.stringify(entries))}\n`)
  const snapshot = h.adapter.load()
  assert.deepEqual(snapshot.entries, entries)
  h.adapter.save(snapshot, { ...entries, newKey: 'user' })
  assert.deepEqual(h.adapter.load().entries, { ...entries, newKey: 'user' })
  assert.ok((await readFile(h.env, 'utf8')).startsWith('OTHER=yes\n'))
})
