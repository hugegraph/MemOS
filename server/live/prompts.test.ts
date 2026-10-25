import test from 'node:test'
import assert from 'node:assert/strict'
import { PromptClient } from './adapters/prompt-client.ts'
import { PromptService } from './services/prompts.ts'
import { ApiError } from './errors.ts'
import type { DashboardInput, RequestOptions } from './types.ts'
import type { RecordedBody } from './test-support.ts'

function setup() {
  const calls: { path: string; body: RecordedBody; options: RequestOptions }[] = [],
    store = new Map<string, string | undefined>()
  const client = {
    async request(path: string, value?: unknown, options?: RequestOptions) {
      const body = value as RecordedBody
      calls.push({ path, body, options: options! })
      const key = JSON.stringify([body.user_id, body.mem_cube_id])
      const action = path.split('/').at(-1)
      if (action === 'save') {
        store.set(key, body.prompt)
        return { data: { ok: true } }
      }
      if (action === 'reset-default') {
        store.set(key, '')
        return { data: { prompt: '' } }
      }
      if (action === 'generate') return { data: { prompt: 'draft' } }
      if (action === 'extract')
        return { data: { extractionTypes: ['text_mem'], results: [{ type: 'text_mem', content: 'fixture extraction' }] } }
      return { data: { prompt: store.get(key) ?? '', updatedAt: '2026-10-19T00:00:00Z' } }
    },
  }
  const workspaces = {
    resolveWorkspace(id: string) {
      if (!['A', 'B'].includes(id)) throw new ApiError('Unknown project', 40001, 404)
      return { principal: { userId: 'same-owner', cubeId: `cube-${id}` } }
    },
  }
  const adapter = new PromptClient(client, 'test-only-private-token')
  return { service: new PromptService(workspaces, adapter), adapter, client, workspaces, calls }
}
test('prompt scope: same owner distinct cubes, no caller identity override or hidden local copy', async () => {
  const h = setup()
  await h.service.execute('save', { projectId: 'A', prompt: 'rule A', user_id: 'attacker', mem_cube_id: 'cube-B' })
  assert.equal((await h.service.execute('detail', { projectId: 'B' })).prompt, '')
  const restarted = new PromptService(h.workspaces, h.adapter)
  assert.equal((await restarted.execute('detail', { projectId: 'A' })).prompt, 'rule A')
  assert.equal(h.calls[0].body.mem_cube_id, 'cube-A')
  assert.equal(h.calls[0].options.headers!['X-Dashboard-Prompt-Token'], 'test-only-private-token')
  assert.equal(h.calls[0].options.timeout, 120000)
})
test('prompt generate is draft-only; reset persists native default and debug is scoped', async () => {
  const h = setup()
  await h.service.execute('save', { projectId: 'A', prompt: 'original' })
  assert.equal((await h.service.execute('generate', { projectId: 'A', description: 'rewrite' })).prompt, 'draft')
  assert.equal((await h.service.execute('detail', { projectId: 'A' })).prompt, 'original')
  const result = await h.service.execute('debug/extract', {
    projectId: 'A',
    messages: [{ role: 'user', content: 'example', extra: 'ignored' }],
  })
  assert.deepEqual(result, { extractionTypes: ['text_mem'], results: [{ type: 'text_mem', content: 'fixture extraction' }] })
  assert.deepEqual(h.calls.at(-1)!.body.messages, [{ role: 'user', content: 'example' }])
  await h.service.execute('reset-default', { projectId: 'A' })
  assert.equal((await h.service.execute('detail', { projectId: 'A' })).prompt, '')
})
test('prompt bounds, unsupported types and unknown projects reject before upstream', async () => {
  const h = setup()
  for (const [action, body] of [
    ['save', { prompt: 'x'.repeat(12001) }],
    ['generate', { description: ' ' }],
    ['debug/extract', { messages: [] }],
    ['debug/extract', { messages: [{ role: 'tool', content: 'x' }] }],
    ['debug/extract', { messages: [{ role: 'user', content: 'x' }], extractionTypes: ['event_mem'] }],
    ['debug/extract', { messages: [{ role: 'user', content: 'x'.repeat(24001) }] }],
  ] satisfies [string, DashboardInput][])
    await assert.rejects(h.service.execute(action, { projectId: 'A', ...body }), (e) => e instanceof ApiError && e.status === 400)
  await assert.rejects(h.service.execute('detail', { projectId: 'missing' }), (e) => e instanceof ApiError && e.status === 404)
  assert.equal(h.calls.length, 0)
})
test('unconfigured transport remains explicitly unsupported; malformed upstream never becomes success', async () => {
  const h = setup(),
    binding = h.workspaces.resolveWorkspace('A')
  await assert.rejects(
    new PromptClient(h.client, '').execute('save', binding, { prompt: 'x' }),
    (e) => e instanceof ApiError && e.status === 501,
  )
  for (const [action, data] of [
    ['save', {}],
    ['detail', { prompt: null }],
    ['debug/extract', { results: [], extractionTypes: [] }],
    ['generate', { prompt: 'x'.repeat(12001) }],
  ] satisfies [string, unknown][])
    await assert.rejects(
      new PromptClient({ request: async () => ({ data }) }, 'test').execute(action, binding, {}),
      (e) => e instanceof ApiError && e.status === 502,
    )
  assert.equal(h.calls.length, 0)
})
