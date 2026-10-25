import { readFile, stat } from 'node:fs/promises'
import { createHash, randomUUID } from 'node:crypto'
import { basename } from 'node:path'
import { pathToFileURL } from 'node:url'
import assert from 'node:assert/strict'
import type { RunnerConfig, RunOptions, RunReport, Endpoint, Workspace, Filters, AuditRow } from './types.ts'
import type { Check, ToolError, Envelope, DashboardResult } from '../../tool-types.ts'
import type { NativeMemory } from '../../../server/live/types.ts'
import type { Memory } from '../../../shared/contract.ts'

// No service addresses, credentials, writes, or subprocess drivers are implicit.
export const requiredCases = [
  'live-identity',
  'memory-oracle',
  'workspace-isolation',
  'empty-workspace',
  'paging-filter',
  'write-read-delete',
  'audit-fields',
  'usage-oracle',
  'gateway-invocation',
  'api-key-e2e',
  'prompt-e2e',
  'browser-e2e',
]
class Blocked extends Error {}
const need: (condition: unknown, message: string) => asserts condition = (condition, message) => {
  if (!condition) throw new Blocked(message)
}
export function aggregate(cases: Check[]) {
  return cases.some((x) => x.required !== false && x.status === 'FAIL')
    ? 'FAIL'
    : cases.some((x) => x.required && x.status === 'BLOCKED')
      ? 'BLOCKED'
      : 'PASS'
}
const bodyText = (row: NativeMemory) => (typeof row.memory === 'string' ? row.memory : JSON.stringify(row.memory ?? ''))
const events: Record<string, string> = {
  add_message: 'addMessage',
  search_memory: 'searchMemory',
  get_user_profile: 'getMemory',
  delete_memory: 'deleteMemory',
  add_feedback: 'addFeedback',
}
const pointer = (value: unknown, path: string) =>
  path
    .split('/')
    .slice(1)
    .reduce<unknown>((v, k) => (v as Record<string, unknown> | null | undefined)?.[k.replaceAll('~1', '/').replaceAll('~0', '~')], value)

export async function run(config?: unknown, options: RunOptions = {}) {
  const report: RunReport = { kind: 'live-http-acceptance', startedAt: new Date().toISOString(), cases: [], cleanup: [], requests: 0 }
  const c = (config || {}) as RunnerConfig // CLI manifests may be incomplete; each case retains its own runtime preconditions.
  const maxCalls = Math.min(500, Math.max(1, c.maxCalls || 100))
  const timeout = Math.min(30000, Math.max(100, c.timeoutMs || 5000))
  const deadline = Date.now() + Math.min(600000, Math.max(1000, c.maxDurationMs || 120000))
  let live = false
  async function test(id: string, fn: () => Promise<unknown>) {
    const required = !['api-key-e2e', 'prompt-e2e'].includes(id)
    try {
      const evidence = await fn()
      report.cases.push({ id, required, status: 'PASS', evidence })
    } catch (e) {
      // Never include response bodies, request content, URLs with credentials, or filesystem content.
      report.cases.push({
        id,
        required,
        status: e instanceof Blocked ? 'BLOCKED' : 'FAIL',
        reason:
          e instanceof Blocked
            ? e.message
            : `Assertion or HTTP failure (${(e as ToolError).code || (e as ToolError).name || 'Error'}); inspect configured service privately`,
      })
    }
  }
  function ready() {
    need(live, 'Live identity not verified; no dependent calls performed')
  }
  async function http<P extends string>(
    service: Endpoint | undefined,
    path: P,
    body?: unknown,
    cleanup = false,
  ): Promise<Envelope<DashboardResult<P>>> {
    need(!options.dryRun && config, 'No explicit manifest or dry-run selected; network disabled')
    need(service?.url, 'Service URL not configured')
    const base = new URL(service.url)
    assert(['http:', 'https:'].includes(base.protocol) && !base.username && !base.password && !base.search && !base.hash)
    assert(path.startsWith('/') && !path.startsWith('//') && !path.includes('://'))
    need(report.requests < maxCalls + (cleanup ? 6 : 0), 'HTTP call budget exhausted')
    need(cleanup || Date.now() < deadline, 'Run time budget exhausted')
    const headers: Record<string, string> = { 'content-type': 'application/json' }
    if (service.bearerEnv) {
      need(process.env[service.bearerEnv], 'Configured bearer environment variable missing')
      headers.authorization = `Bearer ${process.env[service.bearerEnv]}`
    }
    report.requests++
    const response = await fetch(new URL(path, base), {
      method: body === undefined ? 'GET' : 'POST',
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeout),
      redirect: 'error',
    })
    assert(response.ok, 'HTTP status')
    // Bound streamed response size, including servers that omit Content-Length.
    const reader = response.body!.getReader()
    let bytes = 0
    const chunks = []
    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        bytes += value.byteLength
        assert(bytes <= 8 * 1024 * 1024, 'Response limit')
        chunks.push(Buffer.from(value))
      }
    } finally {
      await reader.cancel()
    }
    return JSON.parse(Buffer.concat(chunks).toString('utf8'))
  }
  async function bff<P extends string>(path: P, body?: unknown, cleanup = false): Promise<DashboardResult<P>> {
    const result = await http(c.bff, path, body, cleanup)
    assert.equal(result.code, 0)
    assert('data' in result)
    return result.data
  }
  async function raw(w: Workspace, cleanup = false) {
    assert(w.projectId && w.cubeId && w.userId)
    const r = await http(
      c.upstream,
      '/product/get_memory',
      {
        mem_cube_id: w.cubeId,
        user_id: w.userId,
        include_preference: false,
        include_tool_memory: false,
        include_skill_memory: false,
        page: null,
        page_size: null,
      },
      cleanup,
    )
    assert(Array.isArray(r.data?.text_mem))
    const rows = r.data.text_mem
      .filter((g) => g.cube_id === w.cubeId)
      .flatMap((g) => {
        assert(Array.isArray(g.memories))
        return g.memories
      })
    return [
      ...new Map(
        rows.map((r) => {
          assert(r.id)
          return [String(r.id), r]
        }),
      ).values(),
    ]
  }
  const active = <T extends NativeMemory>(rows: T[]) => rows.filter((r) => (r.metadata?.status ?? 'activated') === 'activated')
  async function list(w: Workspace, extra: Record<string, unknown> | Filters = {}, cleanup = false) {
    const result = await bff(
      '/api/dashboard/project/memory/list',
      { projectId: w.projectId, memoryDetailType: 'text_mem', pageNum: 1, pageSize: 100, ...extra },
      cleanup,
    )
    assert(Array.isArray(result.memListItemResponseList))
    assert(Number.isInteger(result.total))
    assert(Number.isInteger(result.current))
    return result
  }
  function compare(row: NativeMemory, actual: Memory, detail = false) {
    assert.equal(actual.memoryId, String(row.id))
    assert.equal(typeof actual.memoryContent, 'string')
    const content = bodyText(row)
    if (detail || !actual.memoryContent.endsWith('…')) assert.equal(actual.memoryContent, content)
    else {
      assert(actual.memoryContent.length > 1)
      assert(content.startsWith(actual.memoryContent.slice(0, -1)))
    }
    assert.equal(actual.externalUserId, String(row.metadata?.user_id ?? row.metadata?.user_name ?? ''))
    assert.equal(
      actual.subjectType,
      ['user', 'agent', 'public'].includes(row.metadata?.subject_type as string) ? row.metadata!.subject_type : 'user',
    )
    const t = Date.parse(row.metadata?.created_at as string)
    assert.equal(actual.createTime, Number.isFinite(t) ? new Date(t).toISOString() : '')
  }
  async function all(w: Workspace, extra: Record<string, unknown> | Filters = {}) {
    const result = await list(w, extra)
    const rows = [...result.memListItemResponseList]
    need(result.total <= 500, 'Fixture exceeds bounded 500-row enumeration')
    for (let pageNum = 2; rows.length < result.total; pageNum++) {
      const page = await list(w, { ...extra, pageNum })
      assert.equal(page.total, result.total)
      assert(page.memListItemResponseList.length)
      rows.push(...page.memListItemResponseList)
    }
    assert.equal(rows.length, result.total)
    assert.equal(new Set(rows.map((r) => r.memoryId)).size, rows.length)
    return rows
  }
  await test('live-identity', async () => {
    need(config && !options.dryRun, 'Supply --config FILE or MEMOS_ACCEPTANCE_MANIFEST; dry-run never performs HTTP')
    assert.equal((await bff('/api/meta')).mode, 'live', 'Sample/mock mode rejected')
    live = true
    return { mode: 'live' }
  })
  await test('memory-oracle', async () => {
    ready()
    need(c.workspaces?.length, 'Explicit workspace mapping required')
    let checked = 0
    for (const w of c.workspaces) {
      const oracle = active(await raw(w))
      const rows = await all(w)
      assert.deepEqual(rows.map((r) => r.memoryId).sort(), oracle.map((r) => String(r.id)).sort())
      for (const row of oracle)
        compare(
          row,
          rows.find((r) => r.memoryId === String(row.id))!,
        )
      for (const row of oracle.slice(0, 3))
        compare(row, await bff('/api/dashboard/project/memory/detail', { memoryId: String(row.id), memType: 'text_mem' }), true)
      checked += oracle.length
    }
    need(checked > 0, 'No nonempty real fixture; empty-only data cannot prove content parity')
    return { independentlyReadRows: checked }
  })
  await test('workspace-isolation', async () => {
    ready()
    need((c.workspaces?.length as number) >= 2, 'Two real workspaces required')
    const [a, b] = c.workspaces!
    assert.notEqual(a.projectId, b.projectId)
    assert.notEqual(a.cubeId, b.cubeId)
    const ar = active(await raw(a)),
      br = active(await raw(b))
    need(ar.length && br.length, 'Both real workspaces must be nonempty')
    const ai = ar.map((r) => String(r.id)),
      bi = br.map((r) => String(r.id))
    assert(!ai.some((id) => bi.includes(id)))
    assert.deepEqual((await all(a)).map((r) => r.memoryId).sort(), ai.sort())
    assert.deepEqual((await all(b)).map((r) => r.memoryId).sort(), bi.sort())
    return { workspaces: 2, scope: 'memory list selection; not authentication isolation' }
  })
  await test('empty-workspace', async () => {
    ready()
    need(c.emptyWorkspace, 'Dedicated pre-provisioned empty workspace required')
    assert.equal(active(await raw(c.emptyWorkspace)).length, 0)
    assert.equal((await list(c.emptyWorkspace)).total, 0)
    assert.equal((await list(c.emptyWorkspace)).memListItemResponseList.length, 0)
  })
  await test('paging-filter', async () => {
    ready()
    const f = c.frozenFixture
    need(
      f?.workspace && Array.isArray(f.ids) && f.ids.length >= 3 && f.filters?.length,
      'Frozen fixture with >=3 exact IDs and explicit filters required',
    )
    const oracle = active(await raw(f.workspace))
    assert.deepEqual(oracle.map((r) => String(r.id)).sort(), [...f.ids].sort())
    const sorted = oracle
      .map((r) => ({
        r,
        time: Number.isFinite(Date.parse(r.metadata?.created_at as string)) ? new Date(r.metadata!.created_at!).toISOString() : '',
      }))
      .sort((a, b) => b.time.localeCompare(a.time) || String(a.r.id).localeCompare(String(b.r.id)))
      .map((x) => x.r)
    for (let i = 0; i < sorted.length; i += 2) {
      const p = await list(f.workspace, { pageNum: i / 2 + 1, pageSize: 2 })
      assert.equal(p.total, sorted.length)
      assert.equal(p.current, i / 2 + 1)
      assert.deepEqual(
        p.memListItemResponseList.map((r) => r.memoryId),
        sorted.slice(i, i + 2).map((r) => String(r.id)),
      )
    }
    assert.equal((await list(f.workspace, { pageNum: Math.ceil(sorted.length / 2) + 1, pageSize: 2 })).memListItemResponseList.length, 0)
    for (const filter of f.filters) {
      assert(
        Object.keys(filter).length &&
          Object.keys(filter).every((k) => ['externalUserId', 'subjectType', 'memSearchContent', 'startTime', 'endTime'].includes(k)),
      )
      const expected = oracle.filter((r) => {
        const m = r.metadata || {}
        const subject = ['user', 'agent', 'public'].includes(m.subject_type as string) ? m.subject_type : 'user'
        const time = Date.parse(m.created_at as string)
        return (
          (!filter.externalUserId || String(m.user_id ?? m.user_name ?? '') === filter.externalUserId) &&
          (!filter.subjectType || subject === filter.subjectType) &&
          (!filter.memSearchContent ||
            `${bodyText(r)} ${m.skill_name || ''}`.toLowerCase().includes(filter.memSearchContent.toLowerCase())) &&
          (!filter.startTime || time >= Date.parse(filter.startTime)) &&
          (!filter.endTime || time <= Date.parse(filter.endTime))
        )
      })
      need(expected.length > 0 && expected.length < oracle.length, 'Each filter must discriminate a nonempty strict subset')
      assert.deepEqual((await all(f.workspace, filter)).map((r) => r.memoryId).sort(), expected.map((r) => String(r.id)).sort())
    }
    assert.deepEqual(
      active(await raw(f.workspace))
        .map((r) => String(r.id))
        .sort(),
      [...f.ids].sort(),
    )
    return { pages: Math.ceil(sorted.length / 2), filters: f.filters.length }
  })
  await test('write-read-delete', async () => {
    ready()
    const m = c.mutation
    need(
      options.allowMutation && m?.enabled === true && m.workspace?.dedicated === true && /^[a-zA-Z0-9_-]{8,64}$/.test(m.runPrefix || ''),
      'Writes require --allow-mutation, mutation.enabled, dedicated workspace and 8-64 character runPrefix',
    )
    need(
      m.write?.path === '/product/add' && m.write.body && m.write.idsPointer?.startsWith('/'),
      'Explicit /product/add body and inserted-ID JSON pointer required',
    )
    const w = m.workspace
    const marker = `${m.runPrefix}-${randomUUID()}`
    const template = JSON.stringify(m.write.body)
    assert(template.includes('{{marker}}'))
    assert(template.includes('{{cubeId}}'))
    assert(template.includes('{{userId}}'))
    // Substitution through JSON serialization prevents quote injection.
    function replace(value: unknown): unknown {
      if (typeof value === 'string')
        return value.replaceAll('{{marker}}', marker).replaceAll('{{cubeId}}', w.cubeId).replaceAll('{{userId}}', w.userId)
      if (Array.isArray(value)) return value.map(replace)
      if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, replace(v)]))
      return value
    }
    const writeBody = replace(m.write.body) as Record<string, unknown>
    assert.deepEqual(writeBody.writable_cube_ids, [w.cubeId])
    assert.equal(writeBody.user_id, w.userId)
    assert.equal(writeBody.async_mode, 'sync')
    const baseline = await raw(w)
    need(baseline.length === 0, 'Mutation workspace must be empty before writing; existing demo data is never modified')
    let owned: string[] = [],
      attempted = false,
      deleted = false
    try {
      attempted = true
      const response = await http(c.upstream, m.write.path, writeBody)
      const extracted = pointer(response, m.write.idsPointer)
      const ids =
        m.write.idsField && Array.isArray(extracted)
          ? extracted.map((item: Record<string, unknown> | null) => item?.[m.write!.idsField!])
          : extracted
      assert(Array.isArray(ids) && ids.length > 0 && ids.length <= 20 && ids.every((x) => typeof x === 'string' && x.length > 0))
      // Returned insertion IDs also require independent dedicated-cube/marker proof.
      const inserted = [...new Set(ids as string[])]
      let rows: NativeMemory[] = []
      for (let attempt = 0; attempt < 5; attempt++) {
        rows = active(await raw(w))
        if (inserted.every((id) => rows.some((r) => String(r.id) === id))) break
        await new Promise((resolve) => setTimeout(resolve, 200))
      }
      owned = inserted.filter((id) => rows.some((r) => String(r.id) === id && bodyText(r).includes(marker)))
      if (owned.length !== inserted.length)
        report.cleanup.push({
          status: 'BLOCKED',
          reason: 'Some insertion IDs lack independent marker/cube proof; they will not be deleted automatically',
        })
      assert.equal(owned.length, inserted.length)
      for (const id of owned) {
        const row = rows.find((r) => String(r.id) === id)
        assert(row)
        assert(bodyText(row).includes(marker))
        const matches = await list(w, { memSearchContent: marker })
        const item = matches.memListItemResponseList.find((r) => r.memoryId === id)
        assert(item)
        compare(row, item)
        compare(row, await bff('/api/dashboard/project/memory/detail', { memoryId: id, memType: 'text_mem' }), true)
      }
      await bff('/api/dashboard/project/memory/delete', { memory_ids: owned, memType: 'text_mem' })
      assert(!active(await raw(w)).some((r) => owned.includes(String(r.id))))
      deleted = true
      return { insertedAndDeleted: owned.length, markerHash: createHash('sha256').update(marker).digest('hex') }
    } finally {
      if (owned.length && !deleted) {
        try {
          await bff('/api/dashboard/project/memory/delete', { memory_ids: owned, memType: 'text_mem' }, true)
          assert(!active(await raw(w, true)).some((r) => owned.includes(String(r.id))))
          report.cleanup.push({ status: 'PASS', ownedCount: owned.length })
        } catch {
          report.cleanup.push({ status: 'FAIL', reason: 'Owned-ID cleanup failed; manual review required', ownedIds: owned })
        }
      } else if (deleted) report.cleanup.push({ status: 'PASS', ownedCount: owned.length })
      else if (attempted)
        report.cleanup.push({
          status: 'BLOCKED',
          reason: 'Write outcome/IDs unknown; no broad deletion attempted. Review dedicated workspace manually.',
        })
    }
  })
  let auditRows: AuditRow[] | undefined
  async function readAudit() {
    if (auditRows) return auditRows
    need(
      c.audit?.files?.length && c.audit.projectTenants && c.audit.startDate && c.audit.endDate,
      'Local/mounted JSONL paths, projectTenants and UTC date window required; no remote copying',
    )
    assert(c.audit.files.length <= 20)
    const rows: AuditRow[] = []
    for (const path of c.audit.files) {
      assert(/^calls-.*\.jsonl$/.test(basename(path)))
      assert((await stat(path)).size <= 16 * 1024 * 1024)
      const buffer = await readFile(path)
      let offset = 0
      while (offset < buffer.length) {
        const end = buffer.indexOf(10, offset)
        if (end < 0) break
        const line = buffer.subarray(offset, end)
        offset = end + 1
        try {
          const raw = JSON.parse(line.toString())
          if (typeof raw.tenant === 'string' && Number.isFinite(Date.parse(raw.ts))) rows.push({ raw })
        } catch {
          /* Invalid JSON is not an auditable event. */
        }
      }
    }
    auditRows = rows
    return rows
  }
  function scoped(rows: AuditRow[], projectId: string) {
    const tenants = c.audit!.projectTenants[projectId]
    assert(Array.isArray(tenants) && tenants.length)
    const binding = [...(c.workspaces || []), c.emptyWorkspace].find((w) => w?.projectId === projectId)
    need(binding?.cubeId, 'Explicit audit project/cube binding required; never infer it from tenant')
    const start = Date.parse(`${c.audit!.startDate}T00:00:00Z`),
      end = Date.parse(`${c.audit!.endDate}T00:00:00Z`) + 86400000
    assert(Number.isFinite(start) && Number.isFinite(end) && end > start)
    return rows.filter(
      (r) =>
        tenants.includes(r.raw.tenant) && r.raw.cube_id === binding.cubeId && Date.parse(r.raw.ts) >= start && Date.parse(r.raw.ts) < end,
    )
  }
  await test('audit-fields', async () => {
    ready()
    const rows = await readAudit()
    const selected = Object.keys(c.audit!.projectTenants).flatMap((p) => scoped(rows, p))
    need(selected.length, 'No audit records in configured tenant/date window')
    need(
      selected.every((r) => ['tool', 'ts', 'ms', 'input', 'output', 'ok'].every((k) => Object.hasOwn(r.raw, k))),
      'Legacy audit records lack required fields; cannot prove input/output/duration',
    )
    for (const projectId of Object.keys(c.audit!.projectTenants)) {
      const expected = scoped(rows, projectId)
      need(expected.length <= 20, 'Choose a frozen audit window with <=20 records per project for bounded detail validation')
      const page = await bff('/api/dashboard/log/list', {
        projectId,
        startTime: `${c.audit!.startDate}T00:00:00Z`,
        endTime: `${c.audit!.endDate}T23:59:59.999Z`,
        pageNum: 1,
        pageSize: 20,
      })
      assert.equal(page.total, expected.length)
      assert(Array.isArray(page.logs))
      assert.equal(page.logs.length, expected.length)
      assert.equal(new Set(page.logs.map((r) => r.id)).size, expected.length)
      const remaining = [...expected]
      for (const item of page.logs) {
        const d = await bff('/api/dashboard/log/detail', { logId: item.id })
        assert.equal(d.id, item.id)
        assert.equal(d.eventType, item.eventType)
        assert.equal(d.createTime, item.createTime)
        assert.equal(d.duration, item.duration)
        const normalize = (value: unknown): unknown => {
          if (typeof value === 'string') {
            try {
              return JSON.parse(value)
            } catch {
              return value
            }
          }
          return value
        }
        const found = remaining.findIndex(({ raw: r }) => {
          try {
            assert.equal(d.createTime, new Date(r.ts).toISOString())
            assert.equal(d.eventType, events[r.tool] || r.tool)
            assert.equal(d.duration, Number(r.ms))
            assert.equal(d.status, r.ok === true ? 'SUCCESS' : 'FAILED')
            assert.equal(d.cubeId, r.cube_id)
            assert.deepEqual(JSON.parse(d.requestBody!), normalize(r.input))
            assert.deepEqual(JSON.parse(d.responseBody!), normalize(r.output))
            return true
          } catch {
            return false
          }
        })
        assert(found >= 0, 'No corresponding raw audit event; ID generation is deliberately NOT assumed')
        remaining.splice(found, 1)
      }
      assert.equal(remaining.length, 0)
    }
    return { checked: selected.length, oracle: 'raw JSONL event multiset, independent of BFF ID algorithm' }
  })
  await test('usage-oracle', async () => {
    ready()
    const rows = await readAudit()
    let count = 0
    for (const projectId of Object.keys(c.audit!.projectTenants)) {
      const selected = scoped(rows, projectId)
      count += selected.length
      const result = await bff('/api/dashboard/usage/query', { projectId, startDate: c.audit!.startDate, endDate: c.audit!.endDate })
      assert(Array.isArray(result.dailyStats))
      const days = Math.round((Date.parse(c.audit!.endDate) - Date.parse(c.audit!.startDate)) / 86400000) + 1
      assert.equal(result.dailyStats.length, days)
      assert.equal(new Set(result.dailyStats.map((d) => d.date)).size, days)
      for (let i = 0; i < days; i++) {
        const date = new Date(Date.parse(c.audit!.startDate) + i * 86400000).toISOString().slice(0, 10)
        const day = result.dailyStats.find((d) => d.date === date)
        assert(day)
        for (const [tool, key] of Object.entries({ add_message: 'addMessage', search_memory: 'searchMemory', rerank: 'rerank' } as const))
          assert.equal(
            day[key]!.calls,
            selected.filter((r) => r.raw.tool === tool && new Date(r.raw.ts).toISOString().slice(0, 10) === date).length,
          )
      }
    }
    need(count, 'No real audit records to prove nonzero usage attribution')
    return { independentlyCountedRecords: count, scope: 'calls only; token metering not claimed' }
  })
  // Honest hard blockers: an HTTP endpoint or stale evidence file alone does not prove
  // authenticated MCP invocation, real provider execution, credential rotation, or browser UI.
  for (const id of ['gateway-invocation', 'api-key-e2e', 'prompt-e2e', 'browser-e2e'])
    await test(id, async () => {
      throw new Blocked(
        `${id}: no verified live protocol/browser driver implemented; requires separate configured acceptance driver, not a mock or supplied PASS flag`,
      )
    })
  report.status = aggregate([...report.cases, ...report.cleanup.map((x) => ({ ...x, required: true }))])
  report.finishedAt = new Date().toISOString()
  return report
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const args = process.argv.slice(2)
  if (args.includes('--help')) {
    console.log(
      'node scripts/issue-acceptance/real/runner.ts [--config FILE] [--dry-run] [--allow-mutation]\nManifest path alternatively MEMOS_ACCEPTANCE_MANIFEST. JSON report to stdout; exit 0 PASS, 1 FAIL, 2 BLOCKED. Default performs no network. See manifest.example.json. Secrets only through named bearerEnv variables. No subprocesses or service modifications. Required E2E drivers not implemented remain BLOCKED.',
    )
  } else {
    try {
      const index = args.indexOf('--config')
      const path = index < 0 ? process.env.MEMOS_ACCEPTANCE_MANIFEST : args[index + 1]
      assert(index < 0 || (path && !path.startsWith('--')))
      assert(args.every((a, i) => ['--config', '--dry-run', '--allow-mutation'].includes(a) || (index >= 0 && i === index + 1)))
      const report = await run(path ? JSON.parse(await readFile(path, 'utf8')) : undefined, {
        dryRun: args.includes('--dry-run'),
        allowMutation: args.includes('--allow-mutation'),
      })
      console.log(JSON.stringify(report, null, 2))
      process.exitCode = report.status === 'PASS' ? 0 : report.status === 'FAIL' ? 1 : 2
    } catch {
      console.log(
        JSON.stringify({ kind: 'live-http-acceptance', status: 'FAIL', reason: 'Invalid manifest or CLI arguments; no secrets printed' }),
      )
      process.exitCode = 1
    }
  }
}
