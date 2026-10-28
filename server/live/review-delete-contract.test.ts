import test from 'node:test'
import assert from 'node:assert/strict'
import { MemoryService } from './services/memories.ts'
import type { NativeMemoryClient } from './types.ts'

test('delete accepts omitted optional memType and resolves exact IDs across native buckets', async () => {
  const queries: string[] = []
  const deleted: string[][] = []
  const client: NativeMemoryClient = {
    async getMemory() {
      throw new Error('Unbounded read forbidden')
    },
    async queryMemory(binding, input, ids) {
      assert.deepEqual(ids, ['preference-id'])
      assert.equal(binding.principal.cubeId, 'cube')
      queries.push(input.memType!)
      return {
        rows: input.memType === 'pref_mem' ? [{ id: 'preference-id', memory: 'preference' }] : [],
        total: input.memType === 'pref_mem' ? 1 : 0,
      }
    },
    async deleteMemory(ids) {
      deleted.push(ids)
      return { code: 200 }
    },
  }
  const service = new MemoryService(
    {
      all: () => [{ id: 'p', name: 'p', userId: 'owner', cubeId: 'cube' }],
      resolveWorkspace: () => ({ projectId: 'p', auditTenant: 'owner', principal: { userId: 'owner', cubeId: 'cube' } }),
    },
    client,
  )
  assert.deepEqual(await service.delete(['preference-id']), { deleted: 1 })
  assert.deepEqual(queries, ['text_mem', 'pref_mem'])
  assert.deepEqual(deleted, [['preference-id']])
  await assert.rejects(service.delete(['preference-id'], 'invalid'), /未知记忆标签/)
  assert.equal(deleted.length, 1)
})

test('untyped batch still rejects missing IDs and cross-workspace deletion before mutation', async () => {
  let writes = 0
  const service = new MemoryService(
    {
      all: () => ['a', 'b'].map((id) => ({ id, name: id, userId: 'owner', cubeId: id })),
      resolveWorkspace: (id) => {
        assert.ok(typeof id === 'string')
        return { projectId: id, auditTenant: 'owner', principal: { userId: 'owner', cubeId: id } }
      },
    },
    {
      async getMemory() {
        throw new Error('Unbounded read forbidden')
      },
      async queryMemory(binding, input, ids) {
        const expected = binding.principal.cubeId === 'a' ? 'text_mem' : 'pref_mem'
        const rows = input.memType === expected && ids?.includes(binding.projectId) ? [{ id: binding.projectId }] : []
        return { rows, total: rows.length }
      },
      async deleteMemory() {
        writes++
        return { code: 200 }
      },
    },
  )
  await assert.rejects(service.delete(['a', 'absent']), /不存在/)
  await assert.rejects(service.delete(['a', 'b']), /不能跨工作区/)
  assert.equal(writes, 0)
})
