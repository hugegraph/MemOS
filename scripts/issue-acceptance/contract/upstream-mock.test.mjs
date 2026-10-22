import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { setTimeout as delay } from 'node:timers/promises'
import { harness } from '../../test-support.mjs'

test('mock keeps upstream model/graph routes when merging contract fixes', async (t) => {
  const h = await harness(t)
  for (const path of ['/api/dashboard/models', '/api/dashboard/graph/memories?tenant=A', '/api/dashboard/graph/memory?id=A-text_mem-0']) {
    const r = await fetch(h.base + path)
    assert.equal(r.status, 200)
    assert.equal((await r.json()).code, 0)
  }
})

test('mock preserves UTF-8 across request chunks and still rejects malformed JSON', async (t) => {
  const h = await harness(t)
  const name = '中文分片回归'
  const body = Buffer.from(JSON.stringify({ name }))
  const split = body.indexOf(Buffer.from('中')) + 1
  const response = await new Promise((resolve, reject) => {
    const req = http.request(
      h.base + '/api/dashboard/project/create',
      { method: 'POST', headers: { 'Content-Type': 'application/json' } },
      (res) => {
        const chunks = []
        res.on('data', (chunk) => chunks.push(chunk))
        res.on('error', reject)
        res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(Buffer.concat(chunks).toString('utf8')) }))
      },
    )
    req.on('error', reject)
    req.flushHeaders()
    req.write(body.subarray(0, split))
    delay(30)
      .then(() => req.end(body.subarray(split)))
      .catch(reject)
  })
  assert.equal(response.status, 200)
  assert.equal(response.body.code, 0)
  const dict = await (await fetch(h.base + '/api/dashboard/project/dict')).json()
  assert.ok(dict.data.some((row) => row.name === name))
  const bad = await fetch(h.base + '/api/dashboard/project/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{',
  })
  assert.equal(bad.status, 400)
})
