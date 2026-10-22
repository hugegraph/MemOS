import test from 'node:test'
import assert from 'node:assert/strict'
import { writeFile, appendFile, readFile, rename } from 'node:fs/promises'
import { join } from 'node:path'
import { fixture, memory, P } from './fixture.mjs'
const opts = { timeout: 20000 }
const query = { projectId: 'A', memoryDetailType: 'text_mem', pageSize: 10 }
for (const n of [0, 1, 10, 11, 21])
  test(`I1-MEM-PAGE-${n}: exact distinct active paging`, opts, async (t) => {
    const h = await fixture(t, n)
    h.rows.push(
      { cube: 'A', type: 'text_mem', row: memory('archived', 'A', { status: 'archived' }) },
      { cube: 'A', type: 'text_mem', row: memory('deleted', 'A', { status: 'deleted' }) },
    )
    if (n) h.rows.push(structuredClone(h.rows[0]))
    const ids = []
    for (let pageNum = 1; pageNum <= Math.ceil(n / 10) + 1; pageNum++) {
      const r = await h.request(P + 'project/memory/list', { ...query, pageNum })
      assert.equal(r.total, n)
      assert.equal(r.memListItemResponseList.length, Math.max(0, Math.min(10, n - (pageNum - 1) * 10)))
      ids.push(...r.memListItemResponseList.map((x) => x.memoryId))
    }
    assert.equal(new Set(ids).size, n)
  })
test('I1-MEM-FILTER: Unicode, subject, user and inclusive time', opts, async (t) => {
  const h = await fixture(t, 3)
  Object.assign(h.rows[1].row.metadata, { user_id: '独立用户', subject_type: 'agent', created_at: '2026-10-02T12:00:00Z' })
  const r = await h.request(P + 'project/memory/list', {
    ...query,
    externalUserId: '独立用户',
    subjectType: 'agent',
    memSearchContent: '中文 CAFÉ',
    startTime: '2026-10-02T12:00:00Z',
    endTime: '2026-10-02T12:00:00Z',
  })
  assert.deepEqual(
    r.memListItemResponseList.map((x) => x.memoryId),
    ['A-1'],
  )
})
test('I1-MEM-BYTES: projected UTF8 response below 200KB, full detail retained', opts, async (t) => {
  const h = await fixture(t, 21)
  const text = '中文🙂'.repeat(20000)
  h.rows.forEach((x) => {
    x.row.memory = text
  })
  const r = await h.request(P + 'project/memory/list', { ...query, pageSize: 21 })
  assert.ok(Buffer.byteLength(JSON.stringify(r)) < 200000, 'list exceeds 200KB')
  assert.ok(!JSON.stringify(r).includes('embedding'))
  assert.equal((await h.request(P + 'project/memory/detail', { memoryId: 'A-0', memType: 'text_mem' })).memoryContent.length, text.length)
})
test('I1-MEM-DELETE-UNKNOWN: mixed unknown rejected before any write', opts, async (t) => {
  const h = await fixture(t)
  await h.request(P + 'project/memory/delete', { memory_ids: ['A-0', 'foreign'], memType: 'text_mem' }, 'POST', 404)
  assert.equal(h.writes.length, 0)
})
test('I1-MEM-DELETE-SCOPE: approved policy rejects a mixed authorized-cube batch before writing', opts, async (t) => {
  const h = await fixture(t, 1)
  await h.request(P + 'project/memory/delete', { memory_ids: ['A-0', 'B-0'], memType: 'text_mem' }, 'POST', 400)
  assert.equal(h.writes.length, 0)
  assert.ok(h.rows.every((r) => r.row.metadata.status === 'activated'))
})
test('I1-MEM-SOFTDELETE: upstream retains deleted record, UI hides it', opts, async (t) => {
  const h = await fixture(t, 1)
  await h.request(P + 'project/memory/delete', { memory_ids: ['A-0'], memType: 'text_mem' })
  assert.equal(h.rows[0].row.metadata.status, 'deleted')
  assert.equal((await h.request(P + 'project/memory/list', query)).total, 0)
  await h.request(P + 'project/memory/detail', { memoryId: 'A-0', memType: 'text_mem' }, 'POST', 404)
  assert.equal((await h.request(P + 'project/memory/list', { ...query, projectId: 'B' })).total, 1)
})
// Independent oracle: upstream bucket names documented by product API; no invented event/profile mapping.
for (const type of ['text_mem', 'pref_mem', 'tool_mem', 'skill_mem'])
  test(`I1-TAB-${type}: successful correct bucket`, opts, async (t) => {
    const h = await fixture(t, 0)
    h.rows.push({ cube: 'A', type, row: memory(`oracle-${type}`) })
    const r = await h.request(P + 'project/memory/list', { ...query, memoryDetailType: type })
    assert.deepEqual(
      r.memListItemResponseList.map((x) => x.memoryId),
      [`oracle-${type}`],
    )
  })
for (const type of ['event_mem', 'profile_mem'])
  test(`I1-TAB-${type}: explicit metadata.type mapping, no lifecycle inference`, opts, async (t) => {
    // Product decision confirmed by user 2026-10-19. Expected IDs are independent of mapper implementation.
    const h = await fixture(t, 0)
    h.rows.push(
      { cube: 'A', type: 'text_mem', row: memory('marked', 'A', { type }) },
      { cube: 'A', type: 'text_mem', row: memory('rest-marked', 'A', { type: 'fact', dashboard_type: type }) },
      { cube: 'A', type: 'text_mem', row: memory('archived-marked', 'A', { type, status: 'archived' }) },
      { cube: 'A', type: 'text_mem', row: memory('unmarked-user', 'A', { memory_type: 'UserMemory' }) },
      { cube: 'A', type: 'text_mem', row: memory('unmarked-long-term', 'A', { memory_type: 'LongTermMemory' }) },
      { cube: 'B', type: 'text_mem', row: memory('foreign-marked', 'B', { type }) },
    )
    const page = await h.request(P + 'project/memory/list', { ...query, memoryDetailType: type })
    assert.deepEqual(page.memListItemResponseList.map((row) => row.memoryId).sort(), ['marked', 'rest-marked'])
    assert.equal((await h.request(P + 'project/memory/detail', { memoryId: 'marked', memType: type })).memoryId, 'marked')
    await h.request(P + 'project/memory/detail', { memoryId: 'unmarked-user', memType: type }, 'POST', 404)
    await h.request(P + 'project/memory/delete', { memory_ids: ['marked', 'unmarked-user'], memType: type }, 'POST', 404)
    assert.equal(h.writes.length, 0)
    await h.request(P + 'project/memory/delete', { memory_ids: ['marked', 'rest-marked'], memType: type })
    assert.equal(h.writes.length, 1)
    assert.equal((await h.request(P + 'project/memory/list', { ...query, memoryDetailType: type })).total, 0)
  })
test('I1-PROJECT: A/B/empty switch, create, restart, revoke', opts, async (t) => {
  const h = await fixture(t, 1)
  for (const id of ['A', 'B', 'empty']) {
    await h.request(P + 'project/update', { id, status: 'ENABLED' })
    await h.restart()
    const dict = await h.request(P + 'project/dict', {}, 'GET')
    assert.deepEqual(
      dict.filter((x) => x.status === 'ENABLED').map((x) => x.id),
      [id],
    )
    assert.equal((await h.request(P + 'project/memory/list', { ...query, projectId: id })).total, id === 'empty' ? 0 : 1)
  }
  const p = await h.request(P + 'project/create', { name: 'created', description: '持久化' })
  await h.restart()
  assert.ok((await h.request(P + 'project/list')).records.some((x) => x.id === p.id))
  await h.request(P + `project/revoke/${p.id}`)
  await h.restart()
  assert.ok(!(await h.request(P + 'project/dict', {}, 'GET')).some((x) => x.id === p.id))
  assert.equal(h.writes.length, 0)
})
test('I1-KEYS: full token, real env persistence and project isolation', opts, async (t) => {
  const h = await fixture(t)
  const k = await h.request(P + 'keys/create', { projectId: 'A', keyName: 'fixture' })
  assert.ok(k.apiKey.length > 32)
  await h.restart()
  assert.equal(await h.request(P + 'keys/copy', { apiKeyId: k.id }), k.apiKey)
  assert.equal((await h.request(P + 'keys/list', { projectId: 'A' })).records[0].apiKey, k.apiKey)
  assert.equal((await h.request(P + 'keys/list', { projectId: 'B' })).total, 0)
  const env = await readFile(h.env, 'utf8')
  assert.ok(env.includes(k.apiKey))
  assert.ok(env.includes('UNCHANGED=yes'))
  await h.request(P + `keys/revoke/${k.id}`)
  await h.restart()
  assert.ok(!(await readFile(h.env, 'utf8')).includes(k.apiKey))
  await h.request(P + 'keys/copy', { apiKeyId: k.id }, 'POST', 404)
})
test('I1-TEMPLATE: full CRUD and nested persistence', opts, async (t) => {
  const h = await fixture(t)
  const v = await h.request(P + 'profileTemplate/create', {
    projectId: 'A',
    templateName: '初始',
    group: { field: { value: '初始', algorithm_updatable: false } },
  })
  v.group.field.value = '更新'
  v.templateName = 'updated'
  await h.request(P + 'profileTemplate/update', v)
  await h.restart()
  const path = P + `profileTemplate/detail?projectId=A&templateId=${v.templateId}`
  assert.deepEqual((await h.request(path, {}, 'GET')).group, v.group)
  await h.request(path.replace('projectId=A', 'projectId=B'), {}, 'GET', 404)
  assert.equal((await h.request(P + 'profileTemplate/list', { projectId: 'A' })).total, 1)
  await h.request(P + 'profileTemplate/delete', { projectId: 'A', templateId: v.templateId })
  await h.restart()
  await h.request(path, {}, 'GET', 404)
})
const audit = (tenant, day, tool = 'add_message', extra = {}) => ({
  tenant,
  cube_id: tenant,
  ts: `2026-10-0${day}T12:00:00Z`,
  tool,
  ok: true,
  input: { user_id: '用户', agent_id: 'agent' },
  output: '{"ok":true}',
  ...extra,
})
const lines = (rows) => rows.map((x) => JSON.stringify(x) + '\n').join('')
test('I1-LOG: filters, pages, stringified/missing details, append/truncate/rotation', opts, async (t) => {
  const h = await fixture(t)
  const file = join(h.dir, 'calls-test.jsonl')
  const rows = Array.from({ length: 21 }, (_, i) => audit('A', 1, 'add_message', { trace_id: `trace-${i}` }))
  await writeFile(
    file,
    lines([...rows, audit('B', 1), audit('A', 2, 'search_memory', { ok: false, input: undefined, output: undefined })]),
    'utf8',
  )
  const q = {
    projectId: 'A',
    pageSize: 10,
    eventType: 'addMessage',
    subjectType: 'user',
    subjectId: '用户',
    status: 'SUCCESS',
    query: 'trace-',
    startTime: '2026-10-01',
    endTime: '2026-10-01T23:59:59Z',
  }
  const ids = []
  for (let pageNum = 1; pageNum <= 3; pageNum++) {
    const r = await h.request(P + 'log/list', { ...q, pageNum })
    assert.equal(r.total, 21)
    ids.push(...r.logs.map((x) => x.id))
    assert.equal(r.logs.length, pageNum === 3 ? 1 : 10)
  }
  assert.equal(new Set(ids).size, 21)
  const detail = await h.request(P + 'log/detail', { logId: ids[0] })
  assert.deepEqual(JSON.parse(detail.responseBody), { ok: true })
  assert.equal(JSON.parse(detail.requestBody).user_id, '用户')
  const missing = await h.request(P + 'log/list', { projectId: 'A', status: 'FAILED' })
  const d = await h.request(P + 'log/detail', { logId: missing.logs[0].id })
  assert.equal(JSON.parse(d.requestBody), null)
  assert.equal(JSON.parse(d.responseBody), null)
  await appendFile(file, lines([audit('A', 3)]))
  assert.equal((await h.request(P + 'log/list', { projectId: 'A' })).total, 23)
  await writeFile(file, lines([audit('A', 1)]))
  assert.equal((await h.request(P + 'log/list', { projectId: 'A' })).total, 1)
  await rename(file, file + '.old')
  await writeFile(file, lines([audit('B', 2)]))
  assert.equal((await h.request(P + 'log/list', { projectId: 'A' })).total, 0)
})
test('I1-USAGE: exact daily counts and empty days without sample data', opts, async (t) => {
  const h = await fixture(t)
  await writeFile(join(h.dir, 'calls-usage.jsonl'), lines([audit('A', 1), audit('A', 1), audit('A', 2, 'search_memory'), audit('B', 1)]))
  const q = { projectId: 'A', startDate: '2026-10-01', endDate: '2026-10-03', includeDailyDetails: true }
  const r = await h.request(P + 'usage/query', q)
  assert.deepEqual(
    r.dailyStats.map((x) => [x.date, x.addMessage.calls, x.searchMemory.calls]),
    [
      ['2026-10-01', 2, 0],
      ['2026-10-02', 0, 1],
      ['2026-10-03', 0, 0],
    ],
  )
  const empty = await h.request(P + 'usage/query', { ...q, projectId: 'empty' })
  assert.ok(empty.dailyStats.every((x) => x.addMessage.calls === 0 && x.searchMemory.calls === 0))
  assert.deepEqual(
    r.dailyStats.map((d) => d.totalCalls),
    [2, 1, 0],
  )
  assert.deepEqual(
    r.dailyStats.map((d) => d.callsByEvent),
    [{ addMessage: 2 }, { searchMemory: 1 }, {}],
  )
  assert.ok(empty.dailyStats.every((d) => d.totalCalls === 0 && !d.chat && !d.extractMemory && !d.rerank))
})
test('I1-PROMPT-PERSIST: save/read survives BFF restart', opts, async (t) => {
  const h = await fixture(t)
  const prompt = '仅提取明确陈述的事实。'
  await h.request(P + 'prompt-config/save', { projectId: 'A', prompt })
  await h.restart()
  assert.equal((await h.request(P + 'prompt-config/detail', { projectId: 'A' })).prompt, prompt)
})
