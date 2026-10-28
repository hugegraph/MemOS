import test from 'node:test'
import assert from 'node:assert/strict'
import { MemOSClient } from './adapters/memos-client.ts'
import { MemoryService } from './services/memories.ts'
import { config } from './core.ts'
import type { NativeMemory, WorkspaceBinding } from './types.ts'

for (const count of [2000, 10000])
  test(`bounded upstream: ${count} large records do not require a full export`, async () => {
    const binding: WorkspaceBinding = { projectId: 'p', auditTenant: 'owner', principal: { userId: 'owner', cubeId: 'cube' } }
    let received = 0
    const requests: Record<string, unknown>[] = []
    const client = new MemOSClient(config({ DASHBOARD_MEMORY_TOKEN: 'test-private-token' }), {
      fetch: async (url, options) => {
        assert.ok(url.endsWith('/dashboard-memory/query'))
        const body = JSON.parse(options.body!)
        requests.push(body)
        assert.equal(body.user_id, 'owner')
        assert.equal(body.mem_cube_id, 'cube')
        assert.ok(Number.isSafeInteger(body.page_size) && body.page_size <= 1000)
        const ids: string[] = body.ids ?? Array.from({ length: body.page_size }, (_, i) => String((body.page - 1) * body.page_size + i))
        const rows: NativeMemory[] = ids.map((id) => ({
          id,
          memory: body.ids ? 'full detail' : 'large native content'.repeat(120),
          metadata: { status: 'activated', memory_type: 'LongTermMemory' },
        }))
        const response = JSON.stringify({
          code: 200,
          data: { rows, total: body.ids ? ids.length : count, user_id: 'owner', mem_cube_id: 'cube' },
        })
        received += Buffer.byteLength(response)
        return new Response(response)
      },
    })
    const service = new MemoryService(
      { all: () => [{ id: 'p', name: 'p', userId: 'owner', cubeId: 'cube' }], resolveWorkspace: () => binding },
      client,
    )
    const result = await service.list({
      projectId: 'p',
      memoryDetailType: 'text_mem',
      pageNum: 2,
      pageSize: 10,
      memSearchContent: 'needle',
    })
    assert.equal(result.total, count)
    assert.deepEqual(
      result.memListItemResponseList.map((r) => r.memoryId),
      Array.from({ length: 10 }, (_, i) => String(10 + i)),
    )
    assert.equal(requests[0].search, 'needle')
    assert.equal((await service.detail('999', 'text_mem')).memoryContent, 'full detail')
    assert.deepEqual(requests[1].ids, ['999'])
    assert.equal(requests.length, 2)
    assert.ok(received < 100000, `unexpected upstream bytes ${received}`)
  })

test('unsupported private query fails explicitly, never retries full export', async () => {
  let calls = 0
  const client = new MemOSClient(config({ DASHBOARD_MEMORY_TOKEN: 'fixture' }), {
    fetch: async () => {
      calls++
      return new Response('{}', { status: 404 })
    },
  })
  await assert.rejects(
    client.queryMemory({ projectId: 'p', auditTenant: 'u', principal: { userId: 'u', cubeId: 'c' } }, { memoryDetailType: 'text_mem' }),
  )
  assert.equal(calls, 1)
})

test('exact-ID lookup crosses a large unrelated workspace without downloading its records', async () => {
  const bindings = ['large', 'target'].map((cubeId) => ({
    projectId: cubeId,
    auditTenant: 'owner',
    principal: { userId: 'owner', cubeId },
  }))
  const seen: string[] = []
  const client = new MemOSClient(config({ DASHBOARD_MEMORY_TOKEN: 'fixture' }), {
    fetch: async (_url, options) => {
      const b = JSON.parse(options.body!)
      assert.deepEqual(b.ids, ['wanted'])
      seen.push(b.mem_cube_id)
      const rows = b.mem_cube_id === 'large' ? [] : [{ id: 'wanted', memory: 'exact content' }]
      return Response.json({ code: 200, data: { rows, total: rows.length, user_id: b.user_id, mem_cube_id: b.mem_cube_id } })
    },
  })
  const service = new MemoryService(
    {
      all: () => bindings.map((b) => ({ id: b.projectId, name: b.projectId, userId: 'owner', cubeId: b.projectId })),
      resolveWorkspace: (id) => bindings.find((b) => b.projectId === id)!,
    },
    client,
  )
  assert.equal((await service.detail('wanted', 'text_mem')).memoryContent, 'exact content')
  assert.deepEqual(seen, ['large', 'target'])
})

test('unexpected ID, null row, duplicate and forged scope are rejected by query adapter', async () => {
  for (const rows of [
    [null],
    [{ id: 'foreign' }],
    [{ id: 'wanted' }, { id: 'wanted' }],
    [{ id: 'wanted', metadata: { user_name: 'foreign' } }],
  ]) {
    const client = new MemOSClient(config({ DASHBOARD_MEMORY_TOKEN: 'fixture' }), {
      fetch: async () => Response.json({ code: 200, data: { rows, total: rows.length, user_id: 'owner', mem_cube_id: 'cube' } }),
    })
    await assert.rejects(
      client.queryMemory(
        { projectId: 'p', auditTenant: 'owner', principal: { userId: 'owner', cubeId: 'cube' } },
        { memType: 'text_mem' },
        ['wanted'],
      ),
    )
  }
})
