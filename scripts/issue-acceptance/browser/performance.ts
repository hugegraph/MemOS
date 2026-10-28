// Cold synthetic JSONL benchmark through the real BFF parser and rendered production UI.
import assert from 'node:assert/strict'
import http from 'node:http'
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve, extname, sep } from 'node:path'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { buildApp } from '../../../server/live/server.ts'
import { config } from '../../../server/live/core.ts'
import type { ChildProcess } from 'node:child_process'
import type { AddressInfo } from 'node:net'
import type { BrowserReport } from './types.ts'

async function listen(server: http.Server) {
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  return `http://127.0.0.1:${(server.address() as AddressInfo).port}`
}
async function close(server?: http.Server) {
  if (!server) return
  server.closeAllConnections()
  await new Promise((ok) => server.close(ok))
}

export async function performanceCase(output: string, { missingFields = false } = {}) {
  const dir = await mkdtemp(join(tmpdir(), 'memos-ui-perf-'))
  const dist = resolve('dist')
  let bff: http.Server | undefined, web: http.Server | undefined, child: ChildProcess | undefined
  try {
    await readFile(join(dist, 'index.html')) // Requires pnpm build, not a second UI implementation.
    const lines =
      Array.from({ length: 10000 }, (_, i) =>
        JSON.stringify({
          ts: '2026-10-01T12:00:00Z',
          tenant: 'perf',
          cube_id: 'perf',
          tool: 'search_memory',
          ms: i,
          ok: true,
          ...(missingFields ? {} : { input: { query: `fixture-${i}` }, output: { found: i } }),
        }),
      ).join('\n') + '\n'
    await writeFile(join(dir, 'calls-perf.jsonl'), lines)
    bff = buildApp(
      config({
        DASHBOARD_DATA_DIR: dir,
        MEMORY_CALL_LOG_DIR: dir,
        DASHBOARD_WORKSPACES: JSON.stringify([
          { id: 'perf', name: 'perf', userId: 'perf', cubeId: 'perf', tenant: 'perf', status: 'ENABLED' },
        ]),
      }),
    )
    const api = await listen(bff)
    let logCalls = 0,
      successfulLogCalls = 0,
      rejectedBootstrapCalls = 0
    web = http.createServer(async (req, res) => {
      try {
        const url = new URL(req.url!, 'http://fixture.local')
        if (url.pathname.startsWith('/api/')) {
          if (url.pathname === '/api/dashboard/log/list') logCalls++
          const chunks = []
          for await (const chunk of req) chunks.push(chunk)
          const upstream = await fetch(api + url.pathname + url.search, {
            method: req.method,
            headers: { 'Content-Type': 'application/json' },
            body: ['GET', 'HEAD'].includes(req.method!) ? undefined : Buffer.concat(chunks),
            signal: AbortSignal.timeout(10000),
          })
          if (url.pathname === '/api/dashboard/log/list') {
            if (upstream.status === 200) successfulLogCalls++
            else {
              assert.equal(upstream.status, 400)
              assert.ok(!JSON.parse(Buffer.concat(chunks).toString()).projectId)
              rejectedBootstrapCalls++
            }
          }
          res.writeHead(upstream.status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
          return res.end(Buffer.from(await upstream.arrayBuffer()))
        }
        let file = resolve(dist, '.' + decodeURIComponent(url.pathname))
        assert.ok(file === dist || file.startsWith(dist + sep))
        if (!extname(file)) file = join(dist, 'index.html')
        const data = await readFile(file)
        const type =
          (
            { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png' } as Record<
              string,
              string
            >
          )[extname(file)] || 'application/octet-stream'
        res.writeHead(200, { 'Content-Type': type, 'Cache-Control': 'no-store' })
        res.end(data)
      } catch {
        res.writeHead(502, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ code: 50200, data: null, message: 'Isolated fixture unavailable' }))
      }
    })
    const base = await listen(web)
    const childOutput = join(output, missingFields ? 'missing-fields-browser' : 'performance-browser')
    const env = {
      ...process.env,
      ISSUE_WEB_BASE: base,
      ISSUE_API_BASE: base,
      ISSUE_CASES: 'UI-LOG-10000',
      ISSUE_LOG_FIXTURE: '10000',
      ISSUE_LOG_MISSING_FIELDS: missingFields ? '1' : '',
      ISSUE_REPORT_DIR: childOutput,
      ISSUE_WORKSPACE_MANIFEST: '',
    }
    child = spawn(process.execPath, ['scripts/issue-acceptance/browser/run.ts'], { env, stdio: 'ignore' })
    const timer = setTimeout(() => child!.kill(), 90000)
    try {
      await once(child, 'close')
    } finally {
      clearTimeout(timer)
    }
    const report: BrowserReport = JSON.parse(await readFile(join(childOutput, 'browser.json'), 'utf8'))
    const row = report.rows.find((r) => r.id === 'UI-LOG-10000')
    assert.equal(row?.status, 'PASS', row?.reason || 'Performance browser did not complete')
    assert.equal(successfulLogCalls, 1, 'Only one successful log query; no prewarming')
    assert.equal(logCalls, successfulLogCalls + rejectedBootstrapCalls)
    return {
      ...(row!.evidence as Record<string, unknown>),
      scope: 'isolated synthetic 10000 JSONL → real BFF parser → production DOM',
      coldLogCalls: successfulLogCalls,
      logHttpCalls: logCalls,
      rejectedBootstrapCalls,
    }
  } finally {
    if (child && child.exitCode === null && child.signalCode === null) child.kill()
    await close(web)
    await close(bff)
    assert.ok(resolve(dir).startsWith(resolve(tmpdir()) + sep + 'memos-ui-perf-'))
    await rm(dir, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 })
  }
}
