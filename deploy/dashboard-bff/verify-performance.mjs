// Isolated synthetic HTTP benchmark, NOT real audit data or browser rendering evidence.
import assert from 'node:assert/strict'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { pathToFileURL } from 'node:url'

const root = process.env.BFF_SOURCE_ROOT || process.cwd()
const { buildApp } = await import(pathToFileURL(join(root, 'server/live/server.mjs')))
const { config } = await import(pathToFileURL(join(root, 'server/live/core.mjs')))
const dir = await mkdtemp(join(tmpdir(), 'memos-audit-http-'))
const report = { kind: 'isolated-synthetic-http-performance', status: 'FAIL', rows: 10000, browser: 'NOT_RUN' }
let server
try {
  const data =
    Array.from({ length: 10000 }, (_, i) =>
      JSON.stringify({
        ts: '2026-10-01T12:00:00Z',
        tenant: 'perf',
        cube_id: 'perf',
        tool: 'search_memory',
        ms: i,
        ok: true,
        input: { query: `fixture-${i}` },
        output: { found: i },
      }),
    ).join('\n') + '\n'
  await writeFile(join(dir, 'calls-perf.jsonl'), data)
  const settings = config({
    DASHBOARD_DATA_DIR: dir,
    MEMORY_CALL_LOG_DIR: dir,
    DASHBOARD_WORKSPACES: JSON.stringify([{ id: 'perf', name: 'perf', userId: 'perf', cubeId: 'perf', tenant: 'perf' }]),
  })
  server = buildApp(settings)
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  report.timings = []
  for (const phase of ['cold', 'warm']) {
    const start = performance.now()
    const response = await fetch(`http://127.0.0.1:${server.address().port}/api/dashboard/log/list`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ projectId: 'perf', pageNum: 1, pageSize: 10 }),
      signal: AbortSignal.timeout(5000),
    })
    assert.equal(response.status, 200)
    const result = await response.json()
    const ms = performance.now() - start
    assert.equal(result.code, 0)
    assert.equal(result.data.total, 10000)
    assert.equal(result.data.logs.length, 10)
    report.timings.push({ phase, ms: Number(ms.toFixed(2)) })
    assert.ok(ms < 1000)
  }
  report.status = 'PASS'
} catch (error) {
  report.failure = { type: error.name }
} finally {
  if (server) {
    server.closeAllConnections()
    await new Promise((resolve) => server.close(resolve))
  }
  await rm(dir, { recursive: true, force: true })
}
console.log(JSON.stringify(report, null, 2))
process.exitCode = report.status === 'PASS' ? 0 : 1
