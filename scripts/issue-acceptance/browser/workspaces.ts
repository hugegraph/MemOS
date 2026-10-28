// Native UI interactions on an explicitly prepared, run-owned real workspace manifest.
import assert from 'node:assert/strict'
import { tableLoading, tableError, picker } from './current-ui.ts'
import { readFile, mkdir, writeFile, rename } from 'node:fs/promises'
import { join } from 'node:path'
import { createHash } from 'node:crypto'
import { storageSnapshot, deletedStatus } from './native-delete-oracle.ts'
import type { BrowserContext, ApiResponse } from './types.ts'
import type {
  WorkspaceManifest,
  OwnedProject,
  OwnedMemory,
  NativeTextMemory,
  DashboardResult,
  Envelope,
  MemoryPage,
} from '../../tool-types.ts'
import type { Project } from '../../../shared/contract.ts'

export async function workspaceCases(ctx: Omit<BrowserContext, 'blocked'> & { base?: string }, file: string) {
  const { apiBase, test, navigate, click, evaluate, waitFor, apiResponsesSince, requests } = ctx
  assert.ok(process.argv.includes('--allow-write'), 'Workspace interaction requires explicit --allow-write')
  const m: WorkspaceManifest = JSON.parse(await readFile(file, 'utf8'))
  assert.equal(m.version, 2)
  assert.equal(m.state, 'prepared')
  assert.match(m.run, /^batch3-[0-9a-f]{8}$/)
  assert.equal(m.bff, process.env.ISSUE_PROXIED_BFF || new URL(apiBase).origin)
  assert.equal(m.projects.length, 3)
  await mkdir(ctx.output, { recursive: true })
  for (const p of m.projects) {
    assert.match(p.id, /^proj_[0-9a-f-]{36}$/)
    assert.ok(p.name.startsWith(m.run + '-') && !m.before.some((old) => old.id === p.id))
    assert.ok(p.memories.every((r) => r.memory.includes(m.run)))
  }
  const [a, b, empty] = ['A', 'B', 'empty'].map((label) => m.projects.find((p) => p.label === label))
  const sdkEvents = m.sdkState === 'prepared' ? m.sdkSeeds.filter((r) => r.type === 'event_mem').length : 0
  assert.ok(a && b && empty && a.memories.length === 3 + sdkEvents && b.memories.length === 1 && empty.memories.length === 0)
  async function call<P extends string>(origin: string, path: P, body?: unknown): Promise<DashboardResult<P>> {
    const r = await fetch(origin + path, {
      method: body ? 'POST' : 'GET',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
      redirect: 'error',
      signal: AbortSignal.timeout(15000),
    })
    assert.equal(r.status, 200)
    const reply = (await r.json()) as Envelope<DashboardResult<P>>
    assert.ok([0, 200].includes(reply.code))
    return reply.data
  }
  const dict = () => call(apiBase, '/api/dashboard/project/dict')
  async function raw(p: { id: string }, user = m.owner) {
    const data = await call(m.upstream, '/product/get_memory', { user_id: user, mem_cube_id: p.id, page: null, page_size: null })
    const rows = data.text_mem.filter((g) => g.cube_id === p.id).flatMap((g) => g.memories)
    assert.ok(rows.length <= 50)
    return rows
  }
  const fingerprint = (rows: NativeTextMemory[]) =>
    createHash('sha256')
      .update(JSON.stringify(rows.map((r) => [r.id, r.memory, r.metadata?.status] as const).sort((a, b) => a[0].localeCompare(b[0]))))
      .digest('hex')
  const current = await dict()
  for (const p of m.projects) assert.equal(current.find((v) => v.id === p.id)?.name, p.name)
  const protectedBefore = await raw({ id: m.protectedId }, m.protectedUserId)
  assert.equal(fingerprint(protectedBefore), m.protectedFingerprint)
  const beforeB = fingerprint(await raw(b))
  const alreadyRemoved = [...(m.legacyHardDeleted || []), ...(m.browserDeleted || [])]
  assert.ok(alreadyRemoved.every((id) => a.memories.some((record) => record.id === id)))
  let expectedA = a.memories.filter((record) => !alreadyRemoved.includes(record.id))
  async function choose(p: Project) {
    const selected = (await dict()).find((v) => v.status === 'ENABLED')
    assert.ok(selected)
    if (selected.id === p.id) return navigate('/cn/memoryList')
    await waitFor(() => evaluate(`!!(${picker(selected.name)})`), 'project picker label')
    const started = Date.now()
    await click(picker(selected.name))
    await waitFor(
      () => evaluate(`[...document.querySelectorAll('button')].some(e => e.innerText.trim() === ${JSON.stringify(p.name)})`),
      'project menu option visible',
    )
    await click(`[...document.querySelectorAll('button')].find(e => e.innerText.trim() === ${JSON.stringify(p.name)})`)
    await waitFor(async () => (await dict()).find((v) => v.status === 'ENABLED')?.id === p.id, 'selected project persisted')
    return started
  }
  async function assertRows(p: OwnedProject, expected: OwnedMemory[], started: number) {
    const texts = expected.map((r) => r.memory)
    try {
      await waitFor(
        () =>
          evaluate(
            `(() => { const t=document.querySelector('tbody'); return !!t && t.getBoundingClientRect().height > 0 && !(${tableLoading}) && ${texts.length ? JSON.stringify(texts) + '.every(s=>t.textContent.includes(s))' : "t.innerText.includes('暂无数据')"}; })()`,
          ),
        `workspace ${p.label} rows rendered`,
      )
    } catch (error) {
      // Only this synthetic-fixture memory page, never a key page or log-body screenshot.
      await mkdir(ctx.output, { recursive: true })
      const image = await ctx.send('Page.captureScreenshot', { format: 'png' })
      await writeFile(join(ctx.output, `workspace-${p.label}.png`), Buffer.from(image.data, 'base64'))
      const summary = await evaluate(
        `({rows:document.querySelectorAll('tbody tr').length,hasError:(${tableError}),hasRun:document.querySelector('tbody')?.innerText.includes(${JSON.stringify(m.run)})})`,
      )
      throw new Error(`${(error as Error).message}; rendered=${JSON.stringify(summary)}`, { cause: error })
    }
    // React Query may reuse an earlier successful response on A→B→A. The exact
    // native ID/content checks below still apply; unrelated/stale data cannot pass.
    const observed = [...requests.values()]
      .filter(
        (r) =>
          r.path.endsWith('/project/memory/list') &&
          r.projectId === p.id &&
          r.memoryType === 'text_mem' &&
          r.pageSize === 10 &&
          r.done &&
          r.status === 200,
      )
      .at(-1)
    assert.ok(observed, 'Cached UI requires a previously observed successful response for this exact workspace/view')
    const replies = await apiResponsesSince<MemoryPage>(
      Math.min(started, observed.started),
      (r) => r.path.endsWith('/project/memory/list') && r.projectId === p.id && r.memoryType === 'text_mem' && r.pageSize === 10,
    )
    const data = replies.at(-1)!.data
    assert.deepEqual(data.memListItemResponseList.map((r) => r.memoryId).sort(), expected.map((r) => r.id).sort())
    assert.equal(data.total, expected.length)
    const rendered = (await evaluate(`document.querySelector('tbody').textContent`)) as string
    for (const other of m.projects.filter((x) => x.id !== p.id))
      for (const record of other.memories) assert.ok(!rendered.includes(record.memory), 'Foreign workspace content remained visible')
  }
  try {
    await test('UI-PROJECT-SWITCH', 'required', 'browser-real', async () => {
      await navigate('/cn/memoryList')
      for (const [p, expected] of [
        [a, expectedA],
        [b, b.memories],
        [empty, []],
        [a, expectedA],
      ] satisfies [OwnedProject, OwnedMemory[]][])
        await assertRows(p, expected, await choose(p))
      return 'Native picker A→B→EMPTY→A; exact API ID sets and DOM contents; no foreign/stale rows'
    })
    await test('UI-NATIVE-TABS-NONEMPTY', 'required', 'browser-real-sdk-fixtures', async () => {
      assert.equal(m.sdkState, 'prepared', 'Native SDK fixtures must be independently verified first')
      await navigate('/cn/memoryList')
      await assertRows(a, expectedA, await choose(a))
      const types = [
        ['text_mem', '事实记忆'],
        ['pref_mem', '偏好记忆'],
        ['event_mem', '事件记忆'],
        ['profile_mem', '用户记忆'],
        ['skill_mem', '技能'],
        ['tool_mem', '工具记忆'],
      ]
      const counts: Record<string, number> = {}
      for (const [type, label] of types) {
        const expected =
          type === 'text_mem'
            ? expectedA
            : ['event_mem', 'profile_mem'].includes(type)
              ? expectedA.filter((r) => r.type === type)
              : m.sdkSeeds.filter((r) => r.type === type)
        assert.ok(expected.length > 0, `${type} requires actual nonempty records`)
        let start
        if (type === 'text_mem') start = await navigate('/cn/memoryList')
        else {
          start = Date.now()
          await click(`[...document.querySelectorAll('[role="tab"]')].find(e=>e.innerText.startsWith(${JSON.stringify(label)}))`)
        }
        const replies: ApiResponse<MemoryPage>[] = await apiResponsesSince<MemoryPage>(
          start,
          (r) => r.memoryType === type && r.pageSize === 10 && r.projectId === a.id,
        )
        const result: MemoryPage = replies.at(-1)!.data
        assert.equal(result.total, expected.length)
        assert.deepEqual(result.memListItemResponseList.map((r) => r.memoryId).sort(), expected.map((r) => r.id).sort())
        for (const record of expected) {
          assert.equal(result.memListItemResponseList.find((r) => r.memoryId === record.id)!.memoryContent, record.memory)
          const detail = await call(apiBase, '/api/dashboard/project/memory/detail', { memoryId: record.id, memType: type })
          assert.equal(detail.memoryId, record.id)
          assert.equal(detail.memoryContent, record.memory)
        }
        await waitFor(
          () =>
            evaluate(
              `${JSON.stringify(expected.map((r) => r.memory))}.every(s=>document.querySelector('tbody')?.textContent.includes(s)) && !(${tableError}) && !(${tableLoading})`,
            ),
          `${label} native records visible`,
        )
        if (type === 'skill_mem') assert.equal(result.memListItemResponseList[0].skillName, m.run + '-format-notes')
        counts[type] = expected.length
      }
      return {
        counts,
        nativeSdkAndRestReadback: true,
        exactApiIdsAndContents: true,
        backendDetailsVerified: true,
        nativeClicksAndDom: true,
        scope: 'Read mapping only; not proof of REST extraction. Original memory-detail button remains its existing placeholder.',
      }
    })
    await test('UI-DELETE-REAL', 'required', 'browser-real', async () => {
      await navigate('/cn/memoryList')
      await assertRows(a, expectedA, await choose(a))
      const target = expectedA[0]
      assert.ok(target)
      const beforeStorage = await storageSnapshot(a, target)
      assert.equal(beforeStorage.storedStatus, 'activated')
      assert.equal(beforeStorage.vectorStatus, 'activated')
      const trigger = `[...document.querySelectorAll('tbody tr')].find(r => r.textContent.includes(${JSON.stringify(target.memory)})).querySelector('button[aria-label="删除"]')`
      await click(trigger)
      await waitFor(() => evaluate(`!!document.querySelector('[role="dialog"]')`), 'delete confirmation')
      const cancelStart = Date.now()
      await click(`[...document.querySelector('[role="dialog"]').querySelectorAll('button')].find(e => e.innerText.trim() === '取消')`)
      await waitFor(() => evaluate(`!document.querySelector('[role="dialog"]')`), 'cancel closes dialog')
      assert.equal([...requests.values()].filter((r) => r.started >= cancelStart && r.path.endsWith('/memory/delete')).length, 0)
      const stillActive = (await raw(a)).find((r) => r.id === target.id)
      assert.ok(stillActive)
      assert.equal(stillActive.metadata?.status, 'activated')
      await click(trigger)
      const confirmStart = Date.now()
      await click(`[...document.querySelector('[role="dialog"]').querySelectorAll('button')].find(e => e.innerText.trim() === '确定')`)
      await waitFor(
        () =>
          evaluate(
            `!document.querySelector('[role="dialog"]') && !document.querySelector('tbody')?.textContent.includes(${JSON.stringify(target.memory)})`,
          ),
        'confirmed row disappears',
      )
      const replies = await apiResponsesSince(confirmStart)
      const deletion = replies.filter((r) => r.path.endsWith('/memory/delete'))
      assert.equal(deletion.length, 1)
      assert.deepEqual(deletion[0].deleteIds, [target.id])
      // Record the action for safe resume even if the independent storage assertion fails.
      m.browserDeleted = [...new Set([...(m.browserDeleted || []), target.id])]
      await writeFile(file + '.tmp', JSON.stringify(m, null, 2))
      await rename(file + '.tmp', file)
      assert.ok(!(await raw(a)).some((r) => r.id === target.id && r.metadata?.status !== 'deleted'))
      const afterStorage = await deletedStatus(a, target)
      for (const field of ['graphFingerprint', 'vectorFingerprint', 'payloadFingerprint', 'edges'] as const)
        assert.equal(afterStorage[field], beforeStorage[field], `Soft deletion changed retained ${field}`)
      await writeFile(join(ctx.output, 'delete-storage.json'), JSON.stringify({ before: beforeStorage, after: afterStorage }, null, 2))
      expectedA = expectedA.filter((r) => r.id !== target.id)
      await assertRows(a, expectedA, await navigate('/cn/memoryList'))
      assert.equal(fingerprint(await raw(b)), beforeB)
      assert.equal(fingerprint(await raw({ id: m.protectedId }, m.protectedUserId)), m.protectedFingerprint)
      return 'Cancel makes no delete; confirm marks only selected owned ID; Neo4j node/edges and Qdrant vector retained, both statuses deleted; reload count and B/demo unchanged'
    })
  } finally {
    await test('UI-WORKSPACE-RESTORE', 'required', 'browser-real', async () => {
      const original = m.before.find((p) => p.status === 'ENABLED')
      assert.ok(original)
      await navigate('/cn/memoryList')
      try {
        await choose(original)
      } catch (error) {
        await call(apiBase, '/api/dashboard/project/update', { id: original.id, status: 'ENABLED' })
        throw new Error('UI restoration failed; REST safety fallback restored original selection', { cause: error })
      }
      assert.equal((await dict()).find((p) => p.status === 'ENABLED')?.id, original.id)
      assert.equal(fingerprint(await raw({ id: m.protectedId }, m.protectedUserId)), m.protectedFingerprint)
      return 'Original selection and protected demo fingerprint restored; fixture cleanup remains separate'
    })
  }
}
