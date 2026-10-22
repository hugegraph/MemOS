import test from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdtemp, writeFile, rm, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import Ajv from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'
import { run, aggregate, requiredCases } from './runner.mjs'

test('configuration example conforms to its published schema', async () => {
  const schema = JSON.parse(await readFile(new URL('./manifest.schema.json', import.meta.url), 'utf8'))
  const example = JSON.parse(await readFile(new URL('./manifest.example.json', import.meta.url), 'utf8'))
  const ajv = new Ajv({ strict: false })
  addFormats(ajv)
  const validate = ajv.compile(schema)
  assert.ok(validate(example), ajv.errorsText(validate.errors))
})

test('audit oracle accepts arbitrary stable BFF IDs and compares raw event content', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'audit-oracle-safety-'))
  try {
    const file = join(dir, 'calls-fixture.jsonl')
    const raw = {
      ts: '2026-10-01T12:00:00Z',
      tenant: 'A',
      cube_id: 'cube-A',
      tool: 'search_memory',
      ms: 7,
      ok: true,
      input: { query: 'fixture' },
      output: { found: 1 },
    }
    await writeFile(file, [raw, { ...raw, cube_id: undefined }, { ...raw, cube_id: 'other-cube' }].map(JSON.stringify).join('\n') + '\n')
    await stub(
      async (url) => {
        const report = await run({
          bff: { url },
          workspaces: [{ projectId: 'A', userId: 'A', cubeId: 'cube-A' }],
          audit: { files: [file], projectTenants: { A: ['A'] }, startDate: '2026-10-01', endDate: '2026-10-01' },
        })
        assert.equal(report.cases.find((c) => c.id === 'audit-fields').status, 'PASS')
      },
      (path) => {
        if (path === '/api/meta') return { code: 0, data: { mode: 'live' } }
        const row = {
          id: 'opaque-independent-id',
          cubeId: 'cube-A',
          createTime: '2026-10-01T12:00:00.000Z',
          eventType: 'searchMemory',
          duration: 7,
          status: 'SUCCESS',
          requestBody: JSON.stringify(raw.input),
          responseBody: JSON.stringify(raw.output),
        }
        if (path.endsWith('/log/list')) return { code: 0, data: { total: 1, logs: [row] } }
        if (path.endsWith('/log/detail')) return { code: 0, data: row }
        return { code: 500, data: null }
      },
    )
  } finally {
    await rm(dir, { recursive: true, force: true })
  }
})

// Harness tests only: these local stubs are NEVER evidence of real acceptance.
async function stub(fn, handler) {
  const calls = []
  const server = createServer(async (req, res) => {
    let text = ''
    for await (const chunk of req) text += chunk
    const body = text ? JSON.parse(text) : undefined
    calls.push({ path: req.url, body })
    res.setHeader('content-type', 'application/json')
    try {
      res.end(JSON.stringify(handler(req.url, body)))
    } catch {
      res.statusCode = 500
      res.end('{}')
    }
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  try {
    await fn(`http://127.0.0.1:${server.address().port}`, calls)
  } finally {
    await new Promise((resolve) => server.close(resolve))
  }
}

test('default and dry-run do not touch HTTP; all required cases report BLOCKED', async () => {
  const original = globalThis.fetch
  globalThis.fetch = () => {
    throw new Error('Unexpected network access')
  }
  try {
    for (const report of [await run(), await run({ bff: { url: 'http://localhost:1' } }, { dryRun: true, allowMutation: true })]) {
      assert.equal(report.requests, 0)
      assert.equal(report.status, 'BLOCKED')
      assert.deepEqual(
        report.cases.map((c) => c.id),
        requiredCases,
      )
      assert(report.cases.every((c) => c.status === 'BLOCKED'))
    }
  } finally {
    globalThis.fetch = original
  }
})

test('status aggregation never converts failed/blocked required cases into PASS', () => {
  assert.equal(aggregate([{ status: 'PASS', required: true }]), 'PASS')
  assert.equal(aggregate([{ status: 'BLOCKED', required: true }]), 'BLOCKED')
  assert.equal(aggregate([{ status: 'BLOCKED', required: false }]), 'PASS')
  assert.equal(
    aggregate([
      { status: 'FAIL', required: false },
      { status: 'PASS', required: true },
    ]),
    'PASS',
  )
  assert.equal(aggregate([{ status: 'FAIL' }, { status: 'BLOCKED', required: true }]), 'FAIL')
})

test('sample mode is rejected before any upstream access or mutation', async () => {
  await stub(
    async (url, calls) => {
      const report = await run({ bff: { url }, upstream: { url }, mutation: { enabled: true } }, { allowMutation: true })
      assert.equal(report.status, 'FAIL')
      assert.equal(report.cases[0].status, 'FAIL')
      assert.deepEqual(
        calls.map((c) => c.path),
        ['/api/meta'],
      )
    },
    () => ({ code: 0, data: { mode: 'sample' } }),
  )
})

test('explicit manifest without CLI write opt-in cannot call insert/delete', async () => {
  await stub(
    async (url, calls) => {
      const report = await run({
        bff: { url },
        upstream: { url },
        mutation: { enabled: true, workspace: { dedicated: true }, runPrefix: 'testrun-prefix' },
      })
      assert.equal(report.cases.find((c) => c.id === 'write-read-delete').status, 'BLOCKED')
      assert.deepEqual(
        calls.map((c) => c.path),
        ['/api/meta'],
      )
    },
    () => ({ code: 0, data: { mode: 'live' } }),
  )
})

test('nonempty mutation workspace is never written or deleted', async () => {
  await stub(
    async (url, calls) => {
      const report = await run(
        {
          bff: { url },
          upstream: { url },
          mutation: {
            enabled: true,
            workspace: { projectId: 'x', cubeId: 'x', userId: 'x', dedicated: true },
            runPrefix: 'testrun-prefix',
            write: {
              path: '/product/add',
              idsPointer: '/ids',
              body: { writable_cube_ids: ['{{cubeId}}'], async_mode: 'sync', user_id: '{{userId}}', memory: '{{marker}}' },
            },
          },
        },
        { allowMutation: true },
      )
      assert.equal(report.cases.find((c) => c.id === 'write-read-delete').status, 'BLOCKED')
      assert.deepEqual(
        calls.map((c) => c.path),
        ['/api/meta', '/product/get_memory'],
      )
    },
    (path) =>
      path === '/api/meta'
        ? { code: 0, data: { mode: 'live' } }
        : { data: { text_mem: [{ cube_id: 'x', memories: [{ id: 'preexisting', memory: 'DO NOT DELETE' }] }] } },
  )
})

test('unknown insertion outcome is recorded separately without broad cleanup', async () => {
  await stub(
    async (url, calls) => {
      const report = await run(
        {
          bff: { url },
          upstream: { url },
          mutation: {
            enabled: true,
            workspace: { projectId: 'x', cubeId: 'x', userId: 'x', dedicated: true },
            runPrefix: 'testrun-prefix',
            write: {
              path: '/product/add',
              idsPointer: '/ids',
              body: { writable_cube_ids: ['{{cubeId}}'], async_mode: 'sync', user_id: '{{userId}}', memory: '{{marker}}' },
            },
          },
        },
        { allowMutation: true },
      )
      assert.equal(report.cases.find((c) => c.id === 'write-read-delete').status, 'FAIL')
      assert.equal(report.cleanup[0].status, 'BLOCKED')
      assert(!calls.some((c) => c.path.includes('delete')))
    },
    (path) =>
      path === '/api/meta'
        ? { code: 0, data: { mode: 'live' } }
        : path === '/product/get_memory'
          ? { data: { text_mem: [] } }
          : { unexpected: 'No IDs' },
  )
})

test('cleanup deletes only returned insertion IDs and preserves original failure', async () => {
  let deleted = false,
    wrote = false,
    marker = ''
  await stub(
    async (url, calls) => {
      const report = await run(
        {
          bff: { url },
          upstream: { url },
          mutation: {
            enabled: true,
            workspace: { projectId: 'x', cubeId: 'x', userId: 'x', dedicated: true },
            runPrefix: 'testrun-prefix',
            write: {
              path: '/product/add',
              idsPointer: '/ids',
              body: { writable_cube_ids: ['{{cubeId}}'], async_mode: 'sync', user_id: '{{userId}}', memory: '{{marker}}' },
            },
          },
        },
        { allowMutation: true },
      )
      assert.equal(report.cases.find((c) => c.id === 'write-read-delete').status, 'FAIL')
      assert.equal(report.cleanup[0].status, 'PASS')
      assert.deepEqual(calls.find((c) => c.path.endsWith('/delete')).body.memory_ids, ['own-id'])
    },
    (path, body) => {
      if (path === '/api/meta') return { code: 0, data: { mode: 'live' } }
      if (path === '/product/add') {
        wrote = true
        marker = body.memory
        return { ids: ['own-id'] }
      }
      if (path.endsWith('/list')) return { code: 500, data: null }
      if (path.endsWith('/delete')) {
        deleted = true
        return { code: 0, data: { deleted: 1 } }
      }
      return { data: { text_mem: [{ cube_id: 'x', memories: wrote && !deleted ? [{ id: 'own-id', memory: marker }] : [] }] } }
    },
  )
})
