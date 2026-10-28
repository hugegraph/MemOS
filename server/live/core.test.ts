import assert from 'node:assert/strict'
import http from 'node:http'
import { once } from 'node:events'
import test from 'node:test'
import { config } from './core.ts'
import { createApp } from './server.ts'
import type { TestContext } from 'node:test'
import type { AddressInfo } from 'node:net'
import type { Envelope, HealthMeta } from './test-support.ts'

async function listen(t: TestContext, server: http.Server) {
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  t.after(() => {
    server.closeAllConnections()
    return new Promise((resolve) => server.close(resolve))
  })
  return `http://127.0.0.1:${(server.address() as AddressInfo).port}`
}
test('meta reports live mode and actual upstream health', async (t) => {
  const upstream = await listen(
    t,
    http.createServer((_req, res) => res.end(JSON.stringify({ status: 'healthy' }))),
  )
  const base = await listen(t, createApp(config({ MEMOS_API_BASE_URL: upstream })))
  const result = (await (await fetch(`${base}/api/meta`)).json()) as Envelope<HealthMeta>
  assert.equal(result.code, 0)
  assert.equal(result.data.mode, 'live')
  assert.equal(result.data.health.status, 'healthy')
})
for (const scenario of ['timeout', 'non-json', 'http-error']) {
  test(`meta exposes ${scenario}, never substitutes sample data`, async (t) => {
    const upstream = await listen(
      t,
      http.createServer((_req, res) => {
        if (scenario === 'timeout') return
        if (scenario === 'http-error') res.statusCode = 503
        res.end(scenario === 'non-json' ? '<html>failure</html>' : '{}')
      }),
    )
    const base = await listen(t, createApp(config({ MEMOS_API_BASE_URL: upstream, MEMOS_TIMEOUT_MS: '50' })))
    const result = (await (await fetch(`${base}/api/meta`)).json()) as Envelope<HealthMeta>
    assert.equal(result.data.mode, 'live')
    assert.equal(result.data.health.status, 'unavailable')
    assert.equal(result.data.health.code, scenario === 'timeout' ? 50400 : 50200)
  })
}
test('invalid body and enum fail before route execution; unimplemented functions are explicit', async (t) => {
  const base = await listen(t, createApp(config()))
  for (const body of ['{broken', 'null', '[]', JSON.stringify({ name: 12 })]) {
    const response = await fetch(`${base}/api/dashboard/project/create`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    })
    assert.equal(response.status, 400)
    assert.notEqual(((await response.json()) as Envelope<unknown>).code, 0)
  }
  const response = await fetch(`${base}/api/dashboard/project/list`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  })
  assert.equal(response.status, 501)
})
test('untrusted browser origins are rejected', async (t) => {
  const base = await listen(t, createApp(config()))
  assert.equal((await fetch(`${base}/api/meta`, { headers: { Origin: 'https://untrusted.example' } })).status, 403)
})
test('bodyless revoke POST used by the shipped frontend is accepted', async (t) => {
  const base = await listen(
    t,
    createApp(config(), {
      'POST /api/dashboard/keys/revoke/{apiKeyId}': ({ params }) => ({ revoked: params[0] }),
    }),
  )
  const response = await fetch(`${base}/api/dashboard/keys/revoke/test-key`, { method: 'POST' })
  assert.equal(response.status, 200)
  assert.equal(((await response.json()) as Envelope<{ revoked: string }>).data.revoked, 'test-key')
})
