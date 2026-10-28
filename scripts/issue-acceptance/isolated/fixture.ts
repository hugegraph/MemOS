// Actual live BFF; only its HTTP upstream is simulated. Not deployed acceptance.
import assert from 'node:assert/strict'
import { mkdtemp, writeFile, rm, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import http from 'node:http'
import { once } from 'node:events'
import Ajv from 'ajv/dist/2020.js'
import formats from 'ajv-formats'
import { config } from '../../../server/live/core.ts'
import { createApp } from '../../../server/live/server.ts'
import { buildRoutes } from '../../../server/live/app.ts'
import { queryFixture } from '../../../server/live/query-fixture.ts'
import type { TestContext } from 'node:test'
import type { AddressInfo } from 'node:net'
import type { ValidateFunction } from 'ajv'
import type { Envelope, DashboardResult, ToolSpec, ResponseSpec, RequestBody } from '../../tool-types.ts'
export const spec: ToolSpec = JSON.parse(await readFile(new URL('../../../openapi.json', import.meta.url), 'utf8'))
const ajv = new Ajv({ strict: false, allErrors: false })
formats(ajv)
const validators = new Map<string, ValidateFunction>()
export const P = '/api/dashboard/'
export function memory(id: string, cube = 'A', extra: Record<string, unknown> = {}) {
  return {
    id,
    memory: `中文 café ${id}`,
    metadata: {
      user_id: cube,
      subject_type: 'user',
      status: 'activated',
      created_at: '2026-10-01T12:00:00Z',
      embedding: Array(2048).fill(1),
      ...extra,
    },
  }
}
export async function fixture(t: TestContext, count = 11) {
  const dir = await mkdtemp(join(tmpdir(), 'issue1-bff-'))
  const servers: http.Server[] = []
  t.after(async () => {
    for (const s of servers.reverse()) {
      s.closeAllConnections()
      await new Promise((r) => s.close(r))
    }
    await rm(dir, { recursive: true, force: true })
  })
  const state = {
    rows: ['A', 'B'].flatMap((cube) =>
      Array.from({ length: count }, (_, i) => ({ cube, type: 'text_mem', row: memory(`${cube}-${i}`, cube) })),
    ),
    writes: [] as RequestBody[],
    prompts: new Map<string, string>(), // Simulated upstream persistence, not BFF-local storage or real-model evidence.
  }
  async function listen(s: http.Server) {
    servers.push(s)
    s.listen(0, '127.0.0.1')
    await once(s, 'listening')
    return `http://127.0.0.1:${(s.address() as AddressInfo).port}`
  }
  const upstream = await listen(
    http.createServer(async (req, res) => {
      try {
        let raw = ''
        for await (const chunk of req) raw += chunk
        const body = raw ? JSON.parse(raw) : {}
        let data
        if (req.url === '/product/dashboard-memory/query') data = queryFixture(state.rows, body)
        else if (req.url === '/product/get_memory')
          data = Object.fromEntries(
            ['text_mem', 'pref_mem', 'tool_mem', 'skill_mem'].map((type) => [
              type,
              [
                {
                  cube_id: body.mem_cube_id,
                  memories: state.rows.filter((x) => x.cube === body.mem_cube_id && x.type === type).map((x) => x.row),
                },
              ],
            ]),
          )
        else if (req.url === '/product/dashboard-memory/soft-delete') {
          assert.equal(req.headers['x-dashboard-prompt-token'], 'fixture-private-prompt-token')
          state.writes.push(body)
          for (const x of state.rows) if (body.memory_ids.includes(x.row.id)) x.row.metadata.status = 'deleted'
          data = { deleted: body.memory_ids.length, soft_deleted: true }
        } else if (req.url!.startsWith('/product/dashboard-prompt/')) {
          if (req.headers['x-dashboard-prompt-token'] !== 'fixture-private-prompt-token') throw new Error('private auth missing')
          const key = JSON.stringify([body.user_id, body.mem_cube_id]),
            action = req.url!.split('/').at(-1)
          if (action === 'save') {
            state.prompts.set(key, body.prompt)
            data = { ok: true }
          } else if (action === 'reset-default') {
            state.prompts.set(key, '')
            data = { prompt: '' }
          } else if (action === 'generate') data = { prompt: 'Fixture generated draft' }
          else if (action === 'extract')
            data = { extractionTypes: ['text_mem'], results: [{ type: 'text_mem', content: 'Fixture extraction' }] }
          else data = { prompt: state.prompts.get(key) ?? '', updatedAt: '2026-10-19T00:00:00Z' }
        } else if (req.url === '/product/create_cube') data = { cube_id: body.cube_id }
        else data = { status: 'healthy' }
        res.setHeader('Content-Type', 'application/json')
        res.end(JSON.stringify({ code: 200, data }))
      } catch {
        res.statusCode = 500
        res.end('{}')
      }
    }),
  )
  const env = join(dir, 'gateway.env')
  await writeFile(env, "UNCHANGED=yes\nMEMOS_TENANTS='{}'\n", 'utf8')
  const settings = config({
    MEMOS_API_BASE_URL: upstream,
    DASHBOARD_DATA_DIR: dir,
    MEMORY_CALL_LOG_DIR: dir,
    DASHBOARD_GATEWAY_ENV: env,
    MEMOS_CUBE_OWNER: 'A',
    DASHBOARD_PROMPT_TOKEN: 'fixture-private-prompt-token',
    DASHBOARD_WORKSPACES: JSON.stringify(['A', 'B', 'empty'].map((id) => ({ id, name: id, cubeId: id, tenant: id, userId: id }))),
  })
  let base = await listen(createApp(settings, buildRoutes(settings)))
  async function request<P extends string>(path: P, body: unknown = {}, method = 'POST', expected = 200): Promise<DashboardResult<P>> {
    const response = await fetch(base + path, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: method === 'GET' ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    })
    assert.equal(response.status, expected, `${method} ${path}: expected ${expected}, got ${response.status}`)
    const result = (await response.json()) as Envelope<DashboardResult<P>>
    if (expected === 200) {
      assert.equal(result.code, 0, `${path}: nonzero success envelope`)
      const route = Object.keys(spec.paths).find((p) => new RegExp(`^${p.replace(/\{[^}]+\}/g, '[^/]+')}$`).test(path.split('?')[0]))
      const key = `${method} ${route}`
      if (!validators.has(key)) {
        let r = spec.paths[route!][method.toLowerCase()].responses!['200']
        if (r.$ref)
          r = r.$ref
            .slice(2)
            .split('/')
            .reduce<unknown>((a, k) => (a as Record<string, unknown>)[k], spec) as ResponseSpec
        validators.set(key, ajv.compile({ ...r.content['application/json'].schema, components: spec.components }))
      }
      const validate = validators.get(key)!
      assert.ok(validate(result), `${key}: ${ajv.errorsText(validate.errors)}`)
    }
    return result.data
  }
  return {
    ...state,
    state,
    dir,
    env,
    request,
    restart: async () => {
      base = await listen(createApp(settings, buildRoutes(settings)))
    },
  }
}
