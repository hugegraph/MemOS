import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createServer } from 'node:net'
import { setTimeout as delay } from 'node:timers/promises'
import test from 'node:test'
import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'
import type { AddressInfo } from 'node:net'
import type { ToolSpec, Schema, ResponseSpec, Envelope, DashboardResult } from './tool-types.ts'
import type { ApiKey, Memory, RequestLog, KnowledgeBase, ProfileTemplate } from '../shared/contract.ts'

// This suite never targets an existing server: all writes use a disposable DB.
test('isolated mock API baseline', { timeout: 60000 }, async (t) => {
  const root = new URL('../', import.meta.url)
  const spec: ToolSpec = JSON.parse(await readFile(new URL('openapi.json', root), 'utf8'))
  const ajv = new Ajv2020({ strict: false, allErrors: true })
  addFormats(ajv)
  const schemaFailures: string[] = []
  function validateSchema(schema: Schema | undefined, value: unknown, label: string) {
    if (!schema) return
    const validate = ajv.compile({ ...schema, components: spec.components })
    if (!validate(value)) schemaFailures.push(`${label}: ${ajv.errorsText(validate.errors)}`)
  }
  const dir = await mkdtemp(join(tmpdir(), 'memos-api-test-'))
  const socket = createServer()
  socket.listen(0, '127.0.0.1')
  await once(socket, 'listening')
  const port = (socket.address() as AddressInfo).port
  await new Promise((resolve) => socket.close(resolve))
  const child = spawn(process.execPath, ['server/mock/server.ts'], {
    cwd: root,
    env: { ...process.env, MOCK_PORT: String(port), MOCK_DB: join(dir, 'db.json') },
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let output = ''
  let spawnError: Error | undefined
  child.on('error', (error) => {
    spawnError = error
  })
  child.stdout.on('data', (chunk) => {
    output += chunk
  })
  child.stderr.on('data', (chunk) => {
    output += chunk
  })
  t.after(async () => {
    if (child.exitCode === null && child.signalCode === null && !spawnError) {
      const exited = once(child, 'exit')
      child.kill()
      await exited
    }
    await rm(dir, { recursive: true, force: true })
  })
  const base = `http://127.0.0.1:${port}`
  let ready = false
  for (let i = 0; i < 100; i++) {
    if (spawnError || child.exitCode !== null) throw new Error(`Mock failed: ${spawnError || output}`)
    try {
      const response = await fetch(`${base}/api/meta`, { signal: AbortSignal.timeout(500) })
      ready = response.ok
    } catch {
      /* Startup may not have bound its socket yet. */
    }
    if (ready) break
    await delay(100)
  }
  assert.ok(ready, `Mock startup timeout: ${output}`)
  const covered = new Set()
  async function call<P extends string>(path: P, body?: unknown, method = 'POST', expectedCode = 0): Promise<DashboardResult<P>> {
    const actualPath = path.split('?')[0]
    const contractPath = Object.keys(spec.paths).find((candidate) => {
      const pattern = candidate.replace(/\{[^}]+\}/g, '[^/]+')
      return new RegExp(`^${pattern}$`).test(actualPath)
    })
    assert.ok(contractPath, `Undocumented test route: ${path}`)
    assert.ok(spec.paths[contractPath][method.toLowerCase()])
    const operation = spec.paths[contractPath][method.toLowerCase()]
    if (expectedCode === 0) validateSchema(operation.requestBody?.content?.['application/json']?.schema, body, `REQUEST ${path}`)
    const response = await fetch(`${base}${path}`, {
      method,
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    })
    assert.equal(response.status, 200, path)
    const result = (await response.json()) as Envelope<DashboardResult<P>>
    assert.equal(result.code, expectedCode, `${path}: ${result.message}`)
    assert.equal(typeof result.message, 'string')
    assert.ok(Object.hasOwn(result, 'data'))
    if (expectedCode === 0) covered.add(`${method} ${contractPath}`)
    if (expectedCode === 0) {
      let responseSpec = operation.responses?.['200']
      if (responseSpec?.$ref)
        responseSpec = responseSpec.$ref
          .slice(2)
          .split('/')
          .reduce<unknown>((value, key) => (value as Record<string, unknown>)[key], spec) as ResponseSpec
      validateSchema(responseSpec?.content?.['application/json']?.schema, result, `RESPONSE ${path}`)
    }
    return result.data
  }
  const p = '/api/dashboard'
  let project: string, key: ApiKey, memory: Memory, log: RequestLog, kb: KnowledgeBase, template: ProfileTemplate
  await t.test('meta identifies sample data; project list uses records', async () => {
    assert.equal((await call('/api/meta', undefined, 'GET')).mode, 'sample')
    const dict = await call(`${p}/project/dict?language=cn`, undefined, 'GET')
    assert.ok(dict.length > 0)
    project = dict[0].id
    const list = await call(`${p}/project/list`, { pageNum: 1, pageSize: 1 })
    assert.equal(list.records.length, 1)
    assert.ok(list.total >= dict.length)
  })
  await t.test('memory: six tabs, projection, paging, filters and payload size', async () => {
    for (const type of ['text_mem', 'pref_mem', 'event_mem', 'skill_mem', 'tool_mem', 'profile_mem']) {
      const query = { projectId: project, memoryDetailType: type, pageSize: 10, pageNum: 1 }
      const list = await call(`${p}/project/memory/list`, query)
      assert.ok(Array.isArray(list.memListItemResponseList))
      assert.equal(list.current, 1)
      assert.ok(Buffer.byteLength(JSON.stringify({ code: 0, data: list, message: 'success' })) < 200000)
      for (const row of list.memListItemResponseList) {
        assert.equal(typeof row.memoryId, 'string')
        assert.equal(typeof row.memoryContent, 'string')
        assert.ok(Number.isFinite(Date.parse(row.createTime)))
        assert.ok(!('metadata' in row) && !('history' in row))
      }
      if (type === 'text_mem') {
        assert.ok(list.total > 10, 'Fixture must exercise pagination')
        memory = list.memListItemResponseList[0]
        const second = await call(`${p}/project/memory/list`, { ...query, pageNum: 2 })
        assert.equal(second.total, list.total)
        assert.ok(second.memListItemResponseList.every((r) => !list.memListItemResponseList.some((a) => a.memoryId === r.memoryId)))
        const filtered = await call(`${p}/project/memory/list`, {
          ...query,
          externalUserId: memory.externalUserId,
          memSearchContent: memory.memoryContent,
        })
        assert.ok(filtered.total > 0)
        assert.ok(
          filtered.memListItemResponseList.every(
            (r) => r.externalUserId === memory.externalUserId && r.memoryContent.includes(memory.memoryContent),
          ),
        )
      }
    }
    assert.deepEqual(await call(`${p}/project/memory/detail`, { memoryId: memory.memoryId, memType: 'text_mem' }), memory)
  })
  await t.test('logs: list, event/status filter and JSON-string details', async () => {
    const list = await call(`${p}/log/list`, { projectId: project, pageSize: 2, pageNum: 1 })
    assert.equal(list.logs.length, 2)
    log = list.logs[0]
    const detail = await call(`${p}/log/detail`, { logId: log.id })
    for (const field of ['requestBody', 'responseBody'] as const) {
      assert.equal(typeof detail[field], 'string')
      assert.doesNotThrow(() => JSON.parse(detail[field]!))
    }
    const filtered = await call(`${p}/log/list`, { projectId: project, eventType: log.eventType, status: log.status })
    assert.ok(filtered.total > 0)
    assert.ok(filtered.logs.every((r) => r.eventType === log.eventType && r.status === log.status))
  })
  await t.test('project create, switch, isolation and revoke', async () => {
    const created = await call(`${p}/project/create`, { name: 'isolated-test', description: 'test', isMultiView: 0 })
    await call(`${p}/project/update`, { id: created.id, name: created.name, status: 'ENABLED' })
    const dict = await call(`${p}/project/dict`, undefined, 'GET')
    assert.deepEqual(
      dict.filter((r) => r.status === 'ENABLED').map((r) => r.id),
      [created.id],
    )
    const empty = await call(`${p}/project/memory/list`, { projectId: created.id, memoryDetailType: 'text_mem' })
    assert.equal(empty.total, 0)
    await call(`${p}/project/revoke/${created.id}`, {})
    assert.ok(!(await call(`${p}/project/dict`, undefined, 'GET')).some((r) => r.id === created.id))
  })
  await t.test('keys: create/list/copy/revoke round trip', async () => {
    key = await call(`${p}/keys/create`, { projectId: project, keyName: 'test-key' })
    const list = await call(`${p}/keys/list`, { projectId: project, pageSize: 1000 })
    assert.equal(list.records.find((r) => r.id === key.id)!.apiKey, key.apiKey)
    assert.equal(await call(`${p}/keys/copy`, { apiKeyId: key.id }), key.apiKey)
    await call(`${p}/keys/revoke/${key.id}`, {})
    await call(`${p}/keys/copy`, { apiKeyId: key.id }, 'POST', 40001)
  })
  await t.test('knowledgebase: all eight documented routes', async () => {
    kb = await call(`${p}/knowledgebase/create`, { knowledgebaseName: 'test-kb', projectIds: [project] })
    await call(`${p}/knowledgebase/update`, { id: kb.kbId, knowledgebaseName: 'updated-kb' })
    assert.equal((await call(`${p}/knowledgebase/detail`, { knowledgeBaseId: kb.kbId })).knowledgebaseName, 'updated-kb')
    assert.ok((await call(`${p}/knowledgebase/list`, { name: 'updated-kb' })).records.some((r) => r.kbId === kb.kbId))
    await call(`${p}/knowledgebase/project/associate`, { projectId: project, knowledgeBaseIds: [kb.kbId] })
    for (const route of ['file/list', 'memory/list'] as const) {
      assert.ok(Array.isArray((await call(`${p}/knowledgebase/${route}`, { knowledgeBaseId: kb.kbId })).records))
    }
    await call(`${p}/knowledgebase/project/remove`, { projectId: project, knowledgeBaseId: kb.kbId, deleteAll: true })
  })
  await t.test('templates: CRUD and list shape', async () => {
    template = await call(`${p}/profileTemplate/create`, { projectId: project, templateName: 'test-template' })
    await call(`${p}/profileTemplate/update`, { projectId: project, templateId: template.templateId, templateName: 'updated' })
    assert.equal(
      (await call(`${p}/profileTemplate/detail?projectId=${project}&templateId=${template.templateId}`, undefined, 'GET')).templateName,
      'updated',
    )
    const list = await call(`${p}/profileTemplate/list`, { projectId: project })
    assert.ok(list.list.some((r) => r.templateId === template.templateId))
    for (const field of ['total', 'current', 'size', 'pages'] as const) assert.equal(typeof list[field], 'number')
    await call(`${p}/profileTemplate/delete`, { projectId: project, templateId: template.templateId })
  })
  await t.test('prompt: five routes (mock persistence, not real extraction)', async () => {
    await call(`${p}/prompt-config/save`, { projectId: project, prompt: 'test-prompt' })
    assert.equal((await call(`${p}/prompt-config/detail`, { projectId: project })).prompt, 'test-prompt')
    for (const route of ['generate', 'reset-default'] as const) {
      assert.equal(typeof (await call(`${p}/prompt-config/${route}`, { projectId: project, description: 'test' })).prompt, 'string')
    }
    assert.ok(
      Array.isArray(
        (
          await call(`${p}/prompt-config/debug/extract`, {
            projectId: project,
            messages: [{ role: 'user', content: 'I prefer concise answers.' }],
          })
        ).results,
      ),
    )
  })
  await t.test('usage, skill and account response shapes', async () => {
    const usage = await call(`${p}/usage/query`, {
      projectId: project,
      startDate: '2026-10-01',
      endDate: '2026-10-03',
      includeDailyDetails: true,
    })
    assert.deepEqual(
      usage.dailyStats.map((r) => r.date),
      ['2026-10-01', '2026-10-02', '2026-10-03'],
    )
    assert.equal(typeof (await call(`${p}/skill/skillMD`, { oss_url: 'test' })), 'string')
    assert.equal(typeof (await call('/api/user/quota/summary', undefined, 'GET')), 'object')
    assert.ok(Array.isArray((await call('/api/user/subscription/details', { cycleId: '', includeAllCycles: true })).cycles))
  })
  await t.test('memory deletion and business errors', async () => {
    await call(`${p}/project/memory/delete`, { memory_ids: [memory.memoryId], memType: 'text_mem' })
    await call(`${p}/project/memory/detail`, { memoryId: memory.memoryId, memType: 'text_mem' }, 'POST', 40001)
    await call(`${p}/project/create`, { name: '' }, 'POST', 40000)
    await call(`${p}/log/detail`, { logId: 'does-not-exist' }, 'POST', 40001)
  })
  await t.test('all OpenAPI operations have successful HTTP smoke coverage', () => {
    const required = Object.entries(spec.paths).flatMap(([path, operations]) =>
      Object.keys(operations)
        .filter((m) => ['get', 'post', 'put', 'patch', 'delete'].includes(m))
        .map((m) => `${m.toUpperCase()} ${path}`),
    )
    assert.equal(required.length, 37)
    assert.deepEqual(
      required.filter((route) => !covered.has(route)),
      [],
    )
    t.diagnostic(`Successful operation coverage: ${covered.size}/${required.length}.`)
  })
  await t.test('SCHEMA: successful request bodies and response envelopes match OpenAPI 3.1', () => {
    assert.deepEqual(schemaFailures, [])
  })
})
