import assert from 'node:assert/strict'
import { mkdtemp, writeFile, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import http from 'node:http'
import { once } from 'node:events'
import test from 'node:test'
import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'
import { config } from './core.ts'
import { createApp } from './server.ts'
import { buildRoutes } from './app.ts'
import { invoke } from './test-support.ts'
import { queryFixture } from './query-fixture.ts'
import type { Envelope, RouteResult } from './test-support.ts'
import type { TestContext } from 'node:test'
import type { AddressInfo } from 'node:net'

interface ResponseSpec {
  $ref?: string
  content: Record<string, { schema: Record<string, unknown> }>
}
interface TestSpec {
  paths: Record<string, Record<string, { responses: Record<string, ResponseSpec> }>>
  components: unknown
}

async function setup(t: TestContext) {
  const dir = await mkdtemp(join(tmpdir(), 'memos-live-test-'))
  t.after(() => rm(dir, { recursive: true, force: true }))
  const memories = ['A', 'B'].flatMap((cube) =>
    Array.from({ length: 12 }, (_, i) => ({
      id: `${cube}-${i}`,
      memory: `中文 marker ${i}`,
      metadata: {
        user_name: cube,
        user_id: cube,
        status: i === 11 ? 'archived' : 'activated',
        created_at: `2026-10-${i < 5 ? '01' : '02'}T12:00:00Z`,
        embedding: Array(1024).fill(1),
      },
    })),
  )
  const deleted: string[] = []
  const upstream = http.createServer(async (req, res) => {
    const chunks: Buffer[] = []
    for await (const chunk of req) chunks.push(chunk)
    const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : {}
    let data
    if (req.url === '/product/dashboard-memory/query')
      data = queryFixture(
        memories.map((row) => ({ cube: row.metadata.user_name, type: 'text_mem', row })),
        body,
      )
    else if (req.url === '/product/get_memory')
      data = {
        text_mem: [{ cube_id: body.mem_cube_id, memories: memories.filter((r) => r.metadata.user_name === body.mem_cube_id) }],
        pref_mem: [],
        tool_mem: [],
        skill_mem: [],
      }
    else if (req.url === '/product/dashboard-memory/soft-delete') {
      assert.equal(req.headers['x-dashboard-prompt-token'], 'fixture-soft-delete-token')
      deleted.push(...body.memory_ids)
      for (const record of memories) if (body.memory_ids.includes(record.id)) record.metadata.status = 'deleted'
      data = { deleted: body.memory_ids.length, soft_deleted: true }
    } else if (req.url === '/product/create_cube') data = { cube_id: body.cube_id, owner_id: body.owner_id }
    else data = { status: 'healthy' }
    res.setHeader('Content-Type', 'application/json')
    res.end(JSON.stringify({ code: 200, data }))
  })
  async function listen(server: http.Server) {
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    t.after(() => {
      server.closeAllConnections()
      return new Promise((resolve) => server.close(resolve))
    })
    return `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  }
  const url = await listen(upstream)
  const gatewayEnv = join(dir, 'gateway.env')
  await writeFile(gatewayEnv, "OTHER_SETTING=preserve\nMEMOS_TENANTS='{}'\n")
  const settings = config({
    MEMOS_API_BASE_URL: url,
    DASHBOARD_DATA_DIR: dir,
    MEMORY_CALL_LOG_DIR: dir,
    DASHBOARD_GATEWAY_ENV: gatewayEnv,
    MEMOS_CUBE_OWNER: 'A',
    DASHBOARD_MEMORY_TOKEN: 'fixture-soft-delete-token',
    DASHBOARD_WORKSPACES: JSON.stringify(['A', 'B'].map((id) => ({ id, name: id, cubeId: id, userId: id, tenant: id }))),
  })
  const routes = buildRoutes(settings)
  const base = await listen(createApp(settings, routes))
  async function request<P extends string>(path: P, body?: unknown, method = 'POST', status = 200): Promise<RouteResult<P>> {
    const response = await fetch(`${base}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: method === 'GET' ? undefined : JSON.stringify(body ?? {}),
    })
    assert.equal(response.status, status, path)
    const result = (await response.json()) as Envelope<RouteResult<P>>
    if (status === 200) {
      const spec: TestSpec = JSON.parse(await readFile(new URL('../../openapi.json', import.meta.url), 'utf8'))
      const match = Object.keys(spec.paths).find((route) =>
        new RegExp(`^${route.replace(/\{[^}]+\}/g, '[^/]+')}$`).test(path.split('?')[0]),
      )
      let responseSpec = spec.paths[match!][method.toLowerCase()].responses['200']
      if (responseSpec.$ref)
        responseSpec = responseSpec.$ref
          .slice(2)
          .split('/')
          .reduce<unknown>((value, key) => (value as Record<string, unknown>)[key], spec) as ResponseSpec
      const ajv = new Ajv2020({ strict: false, allErrors: true })
      addFormats(ajv)
      const validate = ajv.compile({ ...responseSpec.content['application/json'].schema, components: spec.components })
      assert.ok(validate(result), `${path}: ${ajv.errorsText(validate.errors)}`)
    }
    if (status === 200) assert.equal(result.code, 0, result.message)
    else assert.notEqual(result.code, 0)
    return result.data
  }
  return { request, settings, routes, deleted, gatewayEnv, dir }
}
const p = '/api/dashboard'
test('live HTTP: active-only totals, exact pagination, time/search filters and delete upstream', async (t) => {
  const h = await setup(t)
  const q = { projectId: 'A', memoryDetailType: 'text_mem', pageSize: 10 }
  const first = await h.request(`${p}/project/memory/list`, q)
  assert.equal(first.total, 11)
  assert.equal(first.memListItemResponseList.length, 10)
  const second = await h.request(`${p}/project/memory/list`, { ...q, pageNum: 2 })
  assert.equal(second.memListItemResponseList.length, 1)
  assert.equal(new Set([...first.memListItemResponseList, ...second.memListItemResponseList].map((r) => r.memoryId)).size, 11)
  const filtered = await h.request(`${p}/project/memory/list`, { ...q, startTime: '2026-10-02T00:00:00Z', endTime: '2026-10-02T23:59:59Z' })
  assert.equal(filtered.total, 6)
  assert.equal((await h.request(`${p}/project/memory/list`, { ...q, memSearchContent: 'marker 1', externalUserId: 'A' })).total, 2)
  await h.request(`${p}/project/memory/delete`, { memory_ids: ['A-0', 'foreign'], memType: 'text_mem' }, 'POST', 404)
  assert.deepEqual(h.deleted, [])
  await h.request(`${p}/project/memory/delete`, { memory_ids: ['A-0'], memType: 'text_mem' })
  assert.deepEqual(h.deleted, ['A-0'])
  await h.request(`${p}/project/memory/detail`, { memoryId: 'A-0', memType: 'text_mem' }, 'POST', 404)
  assert.equal((await h.request(`${p}/project/memory/list`, q)).total, 10)
  assert.equal((await h.request(`${p}/project/memory/list`, { ...q, projectId: 'B' })).total, 11)
})
test('live HTTP: invalid enums/pages and unsupported types do not succeed', async (t) => {
  const { request } = await setup(t)
  const q = { projectId: 'A', memoryDetailType: 'text_mem' }
  for (const body of [
    { ...q, pageNum: 0 },
    { ...q, pageSize: -1 },
    { ...q, pageSize: 1001 },
    { ...q, pageNum: 1.5 },
    { ...q, memoryDetailType: 'invalid' },
  ])
    await request(`${p}/project/memory/list`, body, 'POST', 400)
  assert.equal((await request(`${p}/project/memory/list`, { ...q, memoryDetailType: 'profile_mem' })).total, 0)
})
test('projects: create upstream cube, switch without name, persist and revoke mapping only', async (t) => {
  const h = await setup(t)
  const project = await h.request(`${p}/project/create`, { name: 'new', description: 'test', isMultiView: 0 })
  await h.request(`${p}/project/update`, { id: project.id, status: 'ENABLED' })
  const persisted = buildRoutes(h.settings)
  assert.ok((await invoke(persisted, 'GET /api/dashboard/project/dict')).some((r) => r.id === project.id && r.status === 'ENABLED'))
  await h.request(`${p}/project/revoke/${project.id}`, {})
  assert.deepEqual(h.deleted, [])
})
test('keys: actual env file write, copy, revoke; preserve unrelated configuration', async (t) => {
  const h = await setup(t)
  const key = await h.request(`${p}/keys/create`, { projectId: 'A', keyName: 'test' })
  assert.equal(await h.request(`${p}/keys/copy`, { apiKeyId: key.id }), key.apiKey)
  const text = await readFile(h.gatewayEnv, 'utf8')
  assert.ok(text.includes(key.apiKey))
  assert.ok(text.includes('OTHER_SETTING=preserve'))
  assert.equal((await h.request(`${p}/keys/list`, { projectId: 'B' })).total, 0)
  await h.request(`${p}/keys/revoke/${key.id}`, {})
  assert.ok(!(await readFile(h.gatewayEnv, 'utf8')).includes(key.apiKey))
})
test('templates: scoped CRUD and persisted nested fields', async (t) => {
  const h = await setup(t)
  const group = { preference: { value: '中文', algorithm_updatable: false } }
  const template = await h.request(`${p}/profileTemplate/create`, { projectId: 'A', templateName: 'test', group })
  const stored = buildRoutes(h.settings)
  const detail = await invoke(stored, 'GET /api/dashboard/profileTemplate/detail', {
    query: new URLSearchParams({ templateId: template.templateId, projectId: 'A' }),
  })
  assert.deepEqual(detail.group, group)
  await h.request(`${p}/profileTemplate/detail?templateId=${template.templateId}&projectId=B`, undefined, 'GET', 404)
  await h.request(`${p}/profileTemplate/delete`, { projectId: 'A', templateId: template.templateId })
  assert.equal((await h.request(`${p}/profileTemplate/list`, { projectId: 'A' })).total, 0)
})
test('placeholders and unsupported prompt are explicit', async (t) => {
  const { request } = await setup(t)
  assert.deepEqual(await request(`${p}/knowledgebase/list`, {}), { records: [], total: 0 })
  assert.equal(await request(`${p}/skill/skillMD`, { oss_url: 'https://untrusted.example' }), '')
  await request(`${p}/prompt-config/save`, { projectId: 'A', prompt: 'new' }, 'POST', 501)
})
