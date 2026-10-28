import assert from 'node:assert/strict'
import test from 'node:test'
import { explicitMemoryType, toDashboardMemory, selectMemoryTab } from './adapters/memory-mapper.ts'
import { workspaceBinding } from './domain/workspace.ts'
import { truncateGraphemes } from './text.ts'

test('native unmarked types map to UI semantics and keep the original type', () => {
  for (const [native, expected] of [
    ['UserMemory', 'profile_mem'],
    ['Context', 'event_mem'],
    ['InsightMemory', 'event_mem'],
  ]) {
    const metadata = { type: 'fact', memory_type: native }
    assert.equal(explicitMemoryType(metadata), expected)
    assert.equal(toDashboardMemory({ id: 'native', memory: 'real shape', metadata }).memoryType, native)
  }
  assert.equal(explicitMemoryType({ type: 'fact', memory_type: 'WorkingMemory' }), undefined)
  assert.equal(explicitMemoryType({ type: 'fact', memory_type: 'LongTermMemory' }), undefined)
  assert.throws(() => explicitMemoryType({ dashboard_type: 'event_mem', memory_type: 'UserMemory' }))
})

test('mismatched native grouping is a visible contract error, never a false empty success', () => {
  assert.throws(() =>
    selectMemoryTab({ text_mem: [{ cube_id: 'legacy-user', memories: [{ id: 'a' }] }] }, 'text_mem', { principal: { cubeId: 'cube' } }),
  )
})

test('audit tenant must match the authenticated gateway user', () => {
  assert.throws(() => workspaceBinding({ id: 'p', userId: 'owner', cubeId: 'cube', tenant: 'different' }))
  assert.equal(workspaceBinding({ id: 'p', userId: 'owner', cubeId: 'cube' }).auditTenant, 'owner')
})

test('preview preserves grapheme boundaries and a strict code-unit budget', () => {
  for (const cluster of ['🙂', '👩🏽‍💻', '🇨🇳', 'e\u0301']) {
    for (let limit = 0; limit <= 16; limit++) {
      const value = cluster.repeat(12)
      const preview = truncateGraphemes(value, limit)
      assert.ok(preview.length <= limit + 1)
      const body = preview.endsWith('…') ? preview.slice(0, -1) : preview
      assert.equal(body.length % cluster.length, 0)
      assert.equal(body, cluster.repeat(body.length / cluster.length))
    }
  }
})
