import test from 'node:test'
import assert from 'node:assert/strict'
import { MemOSClient } from './adapters/memos-client.mjs'
import { config } from './core.mjs'
const binding = { principal: { userId: 'owner', cubeId: 'cube' } }

test('soft delete: missing private configuration makes zero upstream writes, never hard fallback', async () => {
  let calls = 0
  const client = new MemOSClient(config(), {
    fetch: async () => {
      calls++
      throw new Error('must not call')
    },
  })
  await assert.rejects(client.deleteMemory(['owned'], binding), (e) => e.status === 501)
  assert.equal(calls, 0)
})
test('soft delete: unavailable extension never calls legacy physical delete', async () => {
  const calls = []
  const client = new MemOSClient(config({ DASHBOARD_PROMPT_TOKEN: 'fixture-private' }), {
    fetch: async (url) => {
      calls.push(url)
      return new Response('{}', { status: 404 })
    },
  })
  await assert.rejects(client.deleteMemory(['owned'], binding), (e) => e.status === 502)
  assert.equal(calls.length, 1)
  assert.ok(calls[0].endsWith('/product/dashboard-memory/soft-delete'))
})
test('soft delete: require explicit semantic acknowledgment and exact count', async () => {
  for (const data of [{ status: 'success' }, { deleted: 0, soft_deleted: true }, { deleted: 1, soft_deleted: false }]) {
    const client = new MemOSClient(config({ DASHBOARD_PROMPT_TOKEN: 'fixture-private' }), {
      fetch: async () => Response.json({ code: 200, data }),
    })
    await assert.rejects(client.deleteMemory(['owned'], binding), (e) => e.status === 502)
  }
})
