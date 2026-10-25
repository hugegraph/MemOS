import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { once } from 'node:events'
import { readFile, mkdtemp, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import type { AddressInfo } from 'node:net'
import type { TestContext } from 'node:test'
import Ajv from 'ajv/dist/2020.js'
import { ModelsClient } from './adapters/models-client.ts'
import { MemOSClient } from './adapters/memos-client.ts'
import { ApiError, config, operations } from './core.ts'
import { buildApp } from './server.ts'
import type { ModelRecord } from '../../shared/contract.ts'

const row = { role: '记忆抽取', model: 'actual-model', spec: 'OpenAILLM', source: 'components.mem_reader.llm', group: 'memory' }
async function listen(t: TestContext, server: http.Server) {
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  t.after(async () => {
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
  })
  return `http://127.0.0.1:${(server.address() as AddressInfo).port}`
}
test('models: additive route keeps original 37 contracts and uses live authenticated GET', async (t) => {
  assert.equal(operations.length, 38)
  const dir = await mkdtemp(join(tmpdir(), 'models-live-'))
  t.after(() => rm(dir, { recursive: true, force: true }))
  let model = 'actual-model'
  const upstream = await listen(
    t,
    http.createServer((req, res) => {
      assert.equal(req.method, 'GET')
      assert.equal(req.url, '/product/dashboard-memory/models')
      assert.equal(req.headers['x-dashboard-prompt-token'], 'test-token')
      res.end(
        JSON.stringify({
          code: 200,
          data: { records: [{ ...row, model, api_key: 'do-not-leak', api_base: 'http://private/?token=secret' }] },
        }),
      )
    }),
  )
  const base = await listen(
    t,
    buildApp(config({ MEMOS_API_BASE_URL: upstream, DASHBOARD_MEMORY_TOKEN: 'test-token', DASHBOARD_DATA_DIR: dir })),
  )
  const document = JSON.parse(await readFile(new URL('./models.openapi.json', import.meta.url), 'utf8'))
  const validate = new Ajv({ strict: false }).compile<{ code: number; data: { records: ModelRecord[] }; message: string }>(
    document.paths['/api/dashboard/models'].get.responses['200'].content['application/json'].schema,
  )
  for (const name of ['actual-model', 'changed-in-native-runtime']) {
    model = name
    const response = await fetch(base + '/api/dashboard/models')
    assert.equal(response.status, 200)
    assert.equal(response.headers.get('cache-control'), 'no-store')
    const body = await response.json()
    assert.ok(validate(body), JSON.stringify(validate.errors))
    assert.deepEqual(body.data.records, [{ ...row, model: name }])
    assert.ok(!JSON.stringify(body).includes('do-not-leak') && !JSON.stringify(body).includes('api_base'))
  }
  assert.equal((await fetch(base + '/api/dashboard/models', { method: 'POST' })).status, 404)
})
test('models: missing token makes zero upstream calls; missing extension never returns fake catalog', async () => {
  let calls = 0
  const noToken = new ModelsClient(
    {
      request: async () => {
        calls++
        return {}
      },
    },
    '',
  )
  await assert.rejects(noToken.list(), (e) => e instanceof ApiError && e.status === 501)
  assert.equal(calls, 0)
  const client = new ModelsClient(
    {
      request: async () => {
        throw new ApiError('upstream unavailable', 50200, 502)
      },
    },
    'token',
  )
  await assert.rejects(client.list(), (e) => e instanceof ApiError && e.status === 502)
})
for (const data of [
  null,
  {},
  { records: null },
  { records: [{}] },
  { records: [row, row] },
  { records: [{ ...row, group: 'unknown' }] },
  { records: [{ ...row, model: '' }] },
  { records: [{ ...row, spec: 'x'.repeat(513) }] },
  { records: [{ ...row, model: 'bad\nname' }] },
  { records: Array.from({ length: 65 }, (_, i) => ({ ...row, source: String(i) })) },
]) {
  test(`models: malformed native payload fails closed (${JSON.stringify(data).slice(0, 75)})`, async () => {
    const client = new ModelsClient({ request: async () => ({ data }) }, 'token')
    await assert.rejects(client.list(), (e) => e instanceof ApiError && e.status === 502)
  })
}
test('models: valid empty runtime list is not replaced by sample rows', async () => {
  assert.deepEqual(await new ModelsClient({ request: async () => ({ data: { records: [] } }) }, 'token').list(), { records: [] })
})
for (const kind of ['timeout', 'non-json', 'absent-extension']) {
  test(`models: real transport ${kind} is an explicit failure`, async (t) => {
    const url = await listen(
      t,
      http.createServer((_req, res) => {
        if (kind === 'timeout') return
        res.statusCode = kind === 'absent-extension' ? 404 : 200
        res.end(kind === 'non-json' ? 'not-json' : '{}')
      }),
    )
    const client = new ModelsClient(new MemOSClient(config({ MEMOS_API_BASE_URL: url, MEMOS_TIMEOUT_MS: '80' })), 'token')
    await assert.rejects(client.list(), (e) => e instanceof ApiError && e.status === (kind === 'timeout' ? 504 : 502))
  })
}
