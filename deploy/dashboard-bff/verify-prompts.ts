// Real acceptance, explicitly opt-in. Never writes pre-existing workspaces or prints provider data.
// prepare (config + debug + fine writes) -> restart MemOS/BFF -> verify -> cleanup.
import assert from 'node:assert/strict'
import { access, readFile, writeFile, rename } from 'node:fs/promises'
import { randomUUID, createHash } from 'node:crypto'
import type { Project } from '../../shared/contract.ts'
import type { NativeTextMemory, CheckedReport, Envelope, DashboardResult, DashboardCall } from '../../scripts/tool-types.ts'
interface PromptProject {
  id: string
  name: string
  ids: string[]
}
interface PromptManifest {
  version: number
  run: string
  bff: string
  upstream: string
  owner: string
  protectedId: string
  protectedUser: string
  projects: PromptProject[]
  state: string
  before: Project[]
  demoFingerprint: string
  demoPrompt: DashboardResult<'prompt-config/detail'>
  fruit: string
  city: string
  messages: { role: string; content: string }[]
}
const phase = process.argv[2],
  file = process.env.PROMPT_VERIFY_MANIFEST
let m!: PromptManifest,
  stage = 'configuration',
  calls = 0
const deadline = Date.now() + 600000
const report: CheckedReport = { kind: 'real-prompt-acceptance', phase, status: 'FAIL', checks: [] }
const active = (rows: NativeTextMemory[]) => rows.filter((r) => (r.metadata?.status ?? 'activated') === 'activated')
const fingerprint = (rows: NativeTextMemory[]) =>
  createHash('sha256')
    .update(JSON.stringify(rows.map((r) => [r.id, r.memory, r.metadata?.status] as const).sort((a, b) => a[0].localeCompare(b[0]))))
    .digest('hex')
async function persist() {
  await writeFile(file + '.tmp', JSON.stringify(m, null, 2), { mode: 0o600 })
  await rename(file + '.tmp', file!)
}
async function request<P extends string>(base: string, path: P, body?: unknown, expected = 200): Promise<DashboardResult<P> | undefined> {
  assert.ok(++calls <= 100 && Date.now() < deadline, 'Run budget exceeded')
  const r = await fetch(base + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    redirect: 'error',
    signal: AbortSignal.timeout(Math.max(1, Math.min(120000, deadline - Date.now()))),
  })
  assert.equal(r.status, expected, `HTTP ${path}`)
  const b = (await r.json()) as Envelope<DashboardResult<P>>
  if (expected !== 200) {
    assert.notEqual(b.code, 0)
    return
  }
  assert.ok([0, 200].includes(b.code), `Business error ${path}`)
  return b.data
}
const bff = ((path: string, body?: unknown, expected?: number) => request(m.bff, '/api/dashboard/' + path, body, expected)) as DashboardCall
const prompt = <A extends string>(p: { id: string }, action: A, body: Record<string, unknown> = {}) =>
  bff('prompt-config/' + action, { ...body, projectId: p.id }) as Promise<DashboardResult<`prompt-config/${A}`>>
async function raw(id: string, user = m.owner) {
  const result = (await request(m.upstream, '/product/get_memory', { user_id: user, mem_cube_id: id, page: null, page_size: null }))!
  assert.ok(Array.isArray(result.text_mem))
  const rows = result.text_mem.filter((g) => g.cube_id === id).flatMap((g) => g.memories)
  assert.ok(rows.length <= 50, 'Only bounded fixtures are allowed')
  return rows
}
function owned(p: Project) {
  assert.ok(/^proj_[0-9a-f-]{36}$/.test(p.id) && p.name.startsWith(m.run + '-') && !m.before.some((x) => x.id === p.id))
}
async function remember(p: PromptProject) {
  owned(p)
  const rows = await raw(p.id)
  for (const r of rows) {
    assert.ok(typeof r.memory === 'string' && r.memory.includes(m.run), 'Unowned memory: do not auto-clean')
    if (!p.ids.includes(r.id)) p.ids.push(r.id)
  }
  await persist()
  return rows
}
function rule(marker: string) {
  return `Extract ONLY the explicitly stated fact tagged ${marker}. Ignore every other fact. Preserve the exact identifier ${marker} in the extracted value. Output one concise factual memory, not an instruction. Do not infer facts.`
}
function matches(rows: { content?: unknown; memory?: unknown }[], wanted: string, unwanted: string) {
  assert.ok(rows.length > 0, 'Expected nonempty extraction')
  const text = rows.map((r) => r.content ?? r.memory).join('\n')
  assert.ok(text.includes(wanted), 'Expected selected source marker')
  assert.ok(!text.includes(unwanted), 'Unexpected excluded source marker')
}
async function add(p: PromptProject) {
  owned(p)
  const before = active(await raw(p.id)).map((r) => r.id)
  await request(m.upstream, '/product/add', {
    user_id: m.owner,
    writable_cube_ids: [p.id],
    async_mode: 'sync',
    mode: 'fine',
    messages: m.messages,
  })
  return active(await remember(p)).filter((r) => !before.includes(r.id))
}
try {
  assert.ok(['prepare', 'verify', 'cleanup'].includes(phase) && process.argv.includes('--allow-write') && file)
  if (phase === 'prepare') {
    let exists = false
    try {
      await access(file)
      exists = true
    } catch {
      /* new run */
    }
    assert.equal(exists, false, 'Never overwrite an ownership manifest')
    for (const value of [process.env.PROMPT_VERIFY_BFF, process.env.PROMPT_VERIFY_UPSTREAM]) {
      const url = new URL(value!)
      assert.ok(['http:', 'https:'].includes(url.protocol) && !url.username && !url.password)
    }
    m = {
      version: 1,
      run: 'prompt4-' + randomUUID().slice(0, 8),
      bff: process.env.PROMPT_VERIFY_BFF,
      upstream: process.env.PROMPT_VERIFY_UPSTREAM,
      owner: 'dashboard-bff-owner',
      protectedId: 'dashboard-demo',
      protectedUser: 'dashboard-demo',
      projects: [],
      state: 'preparing',
    } as unknown as PromptManifest // The ownership ledger is populated in stages below, before each dependent operation.
    m.before = await bff('project/dict')
    assert.ok(m.before.some((p) => p.id === m.protectedId))
    const demo = await raw(m.protectedId, m.protectedUser)
    assert.ok(demo.length > 0)
    m.demoFingerprint = fingerprint(demo)
    m.demoPrompt = await prompt({ id: m.protectedId }, 'detail')
    m.fruit = m.run + '-ORCHARD'
    m.city = m.run + '-TRAVEL'
    m.messages = [{ role: 'user', content: `Record ${m.fruit}: the gardener grows apples. Record ${m.city}: the traveler visited Oslo.` }]
    await persist()
    for (const label of ['A', 'B']) {
      stage = 'create-' + label
      const p = await bff('project/create', {
        name: m.run + '-' + label,
        description: 'Dedicated Prompt acceptance; safe to unbind after cleanup',
      })
      owned(p)
      m.projects.push({ id: p.id, name: p.name, ids: [] })
      await persist()
      assert.equal((await raw(p.id)).length, 0)
      assert.equal((await prompt(p, 'detail')).prompt, '')
    }
    const [a, b] = m.projects
    stage = 'save-isolated-rules'
    await prompt(a, 'save', { prompt: rule(m.fruit) })
    assert.equal((await prompt(b, 'detail')).prompt, '')
    await prompt(b, 'save', { prompt: rule(m.city) })
    stage = 'generate-draft-only'
    const draft = await prompt(a, 'generate', {
      description: 'Keep only explicitly stated orchard facts, preserve source record identifiers, and omit travel facts.',
    })
    assert.ok(typeof draft.prompt === 'string' && draft.prompt.trim().length > 0)
    assert.equal((await prompt(a, 'detail')).prompt, rule(m.fruit))
    report.checks.push({ name: 'real model generate returns unsaved draft', status: 'PASS' })
    stage = 'parallel-debug-no-writes'
    const [da, db] = await Promise.all([
      prompt(a, 'debug/extract', { messages: m.messages }),
      prompt(b, 'debug/extract', { messages: m.messages }),
    ])
    matches(da.results, m.fruit, m.city)
    matches(db.results, m.city, m.fruit)
    assert.equal((await raw(a.id)).length, 0)
    assert.equal((await raw(b.id)).length, 0)
    report.checks.push({ name: 'concurrent A/B extraction uses separate rules and debug writes no memories', status: 'PASS' })
    stage = 'normal-fine-write-A'
    const ar = await add(a)
    matches(ar, m.fruit, m.city)
    assert.ok(ar.every((r) => r.metadata?.dashboard_prompt_revision === 1))
    stage = 'normal-fine-write-B'
    const br = await add(b)
    matches(br, m.city, m.fruit)
    stage = 'change-rule-and-write-again'
    await prompt(a, 'save', { prompt: rule(m.city) })
    const changed = await add(a)
    matches(changed, m.city, m.fruit)
    assert.ok(changed.every((r) => r.metadata?.dashboard_prompt_revision === 2))
    const displayed = await bff('project/memory/list', { projectId: a.id, memoryDetailType: 'text_mem', pageSize: 100 })
    assert.deepEqual(
      displayed.memListItemResponseList.map((r) => r.memoryId).sort(),
      active(await raw(a.id))
        .map((r) => r.id)
        .sort(),
    )
    report.checks.push({ name: 'normal upstream fine writes adopt saved rule, rule change changes selected fact', status: 'PASS' })
    m.state = 'prepared'
    await persist()
  } else {
    m = JSON.parse(await readFile(file, 'utf8'))
    assert.equal(m.version, 1)
    assert.ok(/^prompt4-[0-9a-f]{8}$/.test(m.run))
    for (const p of m.projects) owned(p)
    if (phase === 'verify') {
      assert.equal(m.state, 'prepared')
      assert.equal(m.projects.length, 2)
      const [a, b] = m.projects
      stage = 'post-restart-persistence'
      assert.equal((await prompt(a, 'detail')).prompt, rule(m.city))
      assert.equal((await prompt(b, 'detail')).prompt, rule(m.city))
      const afterRestart = await prompt(a, 'debug/extract', { messages: m.messages })
      matches(afterRestart.results, m.city, m.fruit)
      stage = 'reset-to-native-default'
      await prompt(a, 'reset-default')
      assert.equal((await prompt(a, 'detail')).prompt, '')
      assert.equal((await prompt(b, 'detail')).prompt, rule(m.city))
      const before = fingerprint(await raw(a.id))
      const native = await prompt(a, 'debug/extract', { messages: m.messages })
      const combined = native.results.map((r) => r.content).join('\n')
      assert.ok(combined.includes(m.fruit) && combined.includes(m.city), 'Native reset should no longer exclude orchard fact')
      assert.equal(fingerprint(await raw(a.id)), before)
      assert.equal(fingerprint(await raw(m.protectedId, m.protectedUser)), m.demoFingerprint)
      assert.deepEqual(await prompt({ id: m.protectedId }, 'detail'), m.demoPrompt)
      m.state = 'verified'
      await persist()
      report.checks.push({ name: 'MemOS/BFF restart persistence; reset changes extraction back; B/demo unchanged', status: 'PASS' })
    } else {
      stage = 'cleanup-only-owned-fixtures'
      assert.ok(m.projects.length <= 2)
      for (const p of m.projects) {
        const current = await bff('project/dict')
        if (!current.some((x) => x.id === p.id)) continue
        assert.equal(current.find((x) => x.id === p.id)!.name, p.name)
        await prompt(p, 'reset-default')
        const remaining = active(await remember(p))
        if (remaining.length) await bff('project/memory/delete', { memory_ids: remaining.map((r) => r.id), memType: 'text_mem' })
        assert.equal(active(await raw(p.id)).length, 0)
        await bff('project/revoke/' + p.id, {})
        await bff('prompt-config/detail', { projectId: p.id }, 404)
      }
      const selected = m.before.find((p) => p.status === 'ENABLED')
      if (selected) await bff('project/update', { id: selected.id, status: 'ENABLED' })
      assert.deepEqual((await bff('project/dict')).map((p) => p.id).sort(), m.before.map((p) => p.id).sort())
      assert.equal(fingerprint(await raw(m.protectedId, m.protectedUser)), m.demoFingerprint)
      assert.deepEqual(await prompt({ id: m.protectedId }, 'detail'), m.demoPrompt)
      m.state = 'cleaned'
      await persist()
      report.checks.push({ name: 'test rules reset, memories soft-deleted, mappings revoked, demo unchanged', status: 'PASS' })
      report.note = 'Empty cube registrations and reset configuration revisions remain; no hard deletion.'
    }
  }
  report.status = 'PASS'
} catch (error) {
  report.failure = { stage, type: (error as Error).name }
  if (m && file) await persist().catch(() => {})
}
report.calls = calls
console.log(JSON.stringify(report, null, 2))
process.exitCode = report.status === 'PASS' ? 0 : 1
