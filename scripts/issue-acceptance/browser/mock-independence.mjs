// Isolated original mock + actual Vite config + read-only BFF backed by real MemOS.
// Vite's default /api proxy points at this owned mock; VITE_API_BASE selects live BFF.
import assert from 'node:assert/strict'
import http from 'node:http'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdtemp, readFile, writeFile, copyFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve, sep } from 'node:path'
import { randomBytes } from 'node:crypto'
import { setTimeout as delay } from 'node:timers/promises'
import { buildApp } from '../../../server/live/server.mjs'
import { config } from '../../../server/live/core.mjs'

async function listen(server) {
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  return `http://127.0.0.1:${server.address().port}`
}
async function close(server) {
  if (server) {
    server.closeAllConnections()
    await new Promise((ok) => server.close(ok))
  }
}
export async function mockIndependence(output) {
  const dir = await mkdtemp(join(tmpdir(), 'memos-ui-mock-'))
  let mock, bff, vite, control, browser
  try {
    const reserve = http.createServer()
    const reserved = await listen(reserve),
      port = new URL(reserved).port
    await close(reserve)
    const source = await readFile('server/mock/server.ts', 'utf8')
    const anchor = 'server.listen(PORT, () => {'
    assert.equal(source.split(anchor).length, 2)
    // Only network binding changes in the disposable copy; mock API/data behavior is original.
    await writeFile(join(dir, 'server.ts'), source.replace(anchor, "server.listen(PORT, '127.0.0.1', () => {"))
    for (const name of ['data.ts', 'store.ts']) await copyFile(join('server/mock', name), join(dir, name))
    await writeFile(join(dir, 'package.json'), '{"type":"module"}')
    mock = spawn(process.execPath, ['--experimental-strip-types', join(dir, 'server.ts')], {
      env: { ...process.env, MOCK_PORT: port, MOCK_DB: join(dir, 'mock-db.json') },
      stdio: 'ignore',
    })
    let ready = false
    for (let i = 0; i < 50; i++) {
      assert.equal(mock.exitCode, null, 'Owned mock exited during startup')
      try {
        ready = (await (await fetch(reserved + '/api/meta', { signal: AbortSignal.timeout(500) })).json()).data.mode === 'sample'
      } catch {
        /* startup only */
      }
      if (ready) break
      await delay(100)
    }
    assert.ok(ready)
    const upstream = process.env.ISSUE_UPSTREAM_BASE || 'http://127.0.0.1:8000'
    const settings = config({
      MEMOS_API_BASE_URL: upstream,
      DASHBOARD_DATA_DIR: join(dir, 'state'),
      MEMORY_CALL_LOG_DIR: join(dir, 'audit'),
      DASHBOARD_WORKSPACES: JSON.stringify([
        {
          id: 'dashboard-demo',
          name: 'Dashboard Demo',
          userId: 'dashboard-demo',
          cubeId: 'dashboard-demo',
          tenant: 'dashboard-demo',
          status: 'ENABLED',
        },
      ]),
    })
    bff = buildApp(settings)
    const api = await listen(bff)
    const previous = process.env.VITE_API_BASE
    try {
      process.env.VITE_API_BASE = api
      const { createServer } = await import('vite')
      vite = await createServer({
        configFile: resolve('vite.config.ts'),
        logLevel: 'silent',
        server: { port: 0, host: '127.0.0.1', strictPort: true, proxy: { '/api': { target: reserved, changeOrigin: true } } },
      })
    } finally {
      if (previous === undefined) delete process.env.VITE_API_BASE
      else process.env.VITE_API_BASE = previous
    }
    await vite.listen()
    const base = `http://127.0.0.1:${vite.httpServer.address().port}`
    settings.origins.add(base)
    assert.equal(vite.config.server.proxy['/api'].target, reserved)
    assert.equal(
      (await (await fetch(base + '/api/meta')).json()).data.mode,
      'sample',
      'Default Vite proxy must demonstrably use owned mock',
    )
    const token = randomBytes(32).toString('hex')
    let stopped = false
    control = http.createServer(async (req, res) => {
      if (req.method !== 'POST' || req.url !== '/stop' || req.headers['x-test-control'] !== token) {
        res.writeHead(403)
        return res.end()
      }
      try {
        assert.ok(!stopped && mock.exitCode === null && mock.signalCode === null)
        const exited = once(mock, 'close')
        mock.kill()
        await exited
        stopped = true
        res.writeHead(200, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ stopped: true }))
      } catch {
        res.writeHead(500)
        res.end('Owned mock stop failed')
      }
    })
    const controller = await listen(control)
    const childOutput = join(output, 'mock-stop-browser')
    const env = {
      ...process.env,
      ISSUE_WEB_BASE: base,
      ISSUE_API_BASE: api,
      ISSUE_CASES: 'UI-STOP-MOCK',
      ISSUE_MOCK_FIXTURE: '1',
      ISSUE_WORKSPACE_MANIFEST: '',
      ISSUE_REPORT_DIR: childOutput,
      ISSUE_CONTROL_URL: controller,
      ISSUE_CONTROL_TOKEN: token,
      ISSUE_MOCK_ORIGIN: reserved,
      ISSUE_UPSTREAM_BASE: upstream,
    }
    browser = spawn(process.execPath, ['scripts/issue-acceptance/browser/run.mjs'], { env, stdio: 'ignore' })
    const timer = setTimeout(() => browser.kill(), 90000)
    try {
      await once(browser, 'close')
    } finally {
      clearTimeout(timer)
    }
    const report = JSON.parse(await readFile(join(childOutput, 'browser.json'), 'utf8'))
    const row = report.rows.find((r) => r.id === 'UI-STOP-MOCK')
    assert.equal(row?.status, 'PASS', row?.reason || 'Mock-stop browser did not complete')
    assert.ok(stopped)
    return {
      ...row.evidence,
      scope:
        'Owned original mock process stopped; actual configured default proxy unavailable; explicit live BFF still reads real MemOS. Existing demo mock untouched.',
    }
  } finally {
    if (browser && browser.exitCode === null && browser.signalCode === null) browser.kill()
    if (mock && mock.exitCode === null && mock.signalCode === null) {
      const exited = once(mock, 'close')
      mock.kill()
      await exited
    }
    await close(control)
    await vite?.close()
    await close(bff)
    assert.ok(resolve(dir).startsWith(resolve(tmpdir()) + sep + 'memos-ui-mock-'))
    await rm(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
  }
}
