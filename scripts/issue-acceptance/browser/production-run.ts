// Serve actual pnpm-build output against the selected real BFF. Avoid development
// StrictMode duplicate effects when measuring production outage/render behavior.
import assert from 'node:assert/strict'
import http from 'node:http'
import { readFile, readdir, writeFile } from 'node:fs/promises'
import { resolve, join, extname, sep } from 'node:path'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { createHash } from 'node:crypto'
import type { ChildProcess } from 'node:child_process'
import type { AddressInfo } from 'node:net'
const api = new URL(process.env.ISSUE_API_BASE!)
assert.ok(['http:', 'https:'].includes(api.protocol) && !api.username && !api.password && !api.search)
const dist = resolve(process.env.ISSUE_DIST || 'dist')
await readFile(join(dist, 'index.html'))
const hashes: Record<string, string> = {}
for (const name of await readdir(join(dist, 'assets')))
  if (/\.(js|css)$/.test(name))
    hashes[name] = createHash('sha256')
      .update(await readFile(join(dist, 'assets', name)))
      .digest('hex')
const web = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url!, 'http://fixture.local')
    if (url.pathname.startsWith('/api/')) {
      const chunks = []
      for await (const chunk of req) chunks.push(chunk)
      const reply = await fetch(new URL(url.pathname + url.search, api), {
        method: req.method,
        headers: { 'Content-Type': 'application/json' },
        body: ['GET', 'HEAD'].includes(req.method!) ? undefined : Buffer.concat(chunks),
        signal: AbortSignal.timeout(30000),
      })
      res.writeHead(reply.status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
      return res.end(Buffer.from(await reply.arrayBuffer()))
    }
    let file = resolve(dist, '.' + decodeURIComponent(url.pathname))
    assert.ok(file === dist || file.startsWith(dist + sep))
    if (!extname(file)) file = join(dist, 'index.html')
    const data = await readFile(file)
    res.writeHead(200, {
      'Content-Type':
        ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' } as Record<string, string>)[
          extname(file)
        ] || 'application/octet-stream',
      'Cache-Control': 'no-store',
    })
    res.end(data)
  } catch {
    res.writeHead(502, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ code: 50200, data: null, message: 'Selected BFF unavailable; no fallback' }))
  }
})
let child: ChildProcess | undefined
try {
  web.listen(0, '127.0.0.1')
  await once(web, 'listening')
  const base = `http://127.0.0.1:${(web.address() as AddressInfo).port}`
  child = spawn(process.execPath, ['scripts/issue-acceptance/browser/run.ts', ...process.argv.slice(2)], {
    env: { ...process.env, ISSUE_WEB_BASE: base, ISSUE_API_BASE: base, ISSUE_PROXIED_BFF: api.origin },
    stdio: 'inherit',
  })
  const timer = setTimeout(() => child!.kill(), 300000)
  let code
  try {
    ;[code] = await once(child, 'close')
  } finally {
    clearTimeout(timer)
  }
  const output = resolve(process.env.ISSUE_REPORT_DIR || '.data/issue-acceptance')
  await writeFile(
    join(output, 'production-build.json'),
    JSON.stringify({ production: true, source: 'unmodified dist assets', hashes, realBff: api.origin }, null, 2),
  )
  process.exitCode = code ?? 1
} finally {
  if (child && child.exitCode === null && child.signalCode === null) child.kill()
  web.closeAllConnections()
  await new Promise((done) => web.close(done))
}
