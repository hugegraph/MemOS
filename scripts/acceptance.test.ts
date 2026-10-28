import assert from 'node:assert/strict'
import test from 'node:test'
import { fixture, harness, ids, types } from './test-support.ts'

const options = { timeout: 15000 }
const query = { projectId: 'A', memoryDetailType: 'text_mem', pageNum: 1, pageSize: 10 }
const listPath = 'project/memory/list'
for (const count of [0, 1, 10, 11, 21]) {
  test(`PAGE-${count}: exact sets, totals, last and overflow pages`, options, async (t) => {
    const db = fixture(Math.max(count, 1))
    if (count === 0) db.memories = db.memories.filter((r) => r.projectId === 'B')
    const { call } = await harness(t, db),
      all = []
    for (let pageNum = 1; pageNum <= Math.ceil(count / 10) + 1; pageNum++) {
      const result = await call(listPath, { ...query, pageNum })
      assert.equal(result.total, count)
      assert.equal(result.current, pageNum)
      assert.equal(result.memListItemResponseList.length, Math.max(0, Math.min(10, count - (pageNum - 1) * 10)))
      all.push(...result.memListItemResponseList)
    }
    assert.deepEqual(ids(all), ids(db.memories.filter((r) => r.projectId === 'A' && r.memoryDetailType === 'text_mem')))
    assert.equal(new Set(ids(all)).size, count)
  })
}
test('MEM-TYPES: six exact distinct type sets', options, async (t) => {
  const db = fixture(2),
    { call } = await harness(t, db)
  for (const memoryDetailType of types) {
    const result = await call(listPath, { ...query, memoryDetailType })
    assert.deepEqual(
      ids(result.memListItemResponseList),
      ids(db.memories.filter((r) => r.projectId === 'A' && r.memoryDetailType === memoryDetailType)),
    )
  }
})
test('MEM-FILTER: Unicode/user/subject combined before paging and no match', options, async (t) => {
  const { call } = await harness(t)
  const result = await call(listPath, {
    ...query,
    memSearchContent: '中文 marker-1 ',
    externalUserId: 'u1',
    subjectType: 'agent',
    pageSize: 1,
  })
  assert.equal(result.total, 1)
  assert.deepEqual(ids(result.memListItemResponseList), ['A-text_mem-1'])
  const empty = await call(listPath, { ...query, memSearchContent: 'never-present-unique-value' })
  assert.equal(empty.total, 0)
  assert.deepEqual(empty.memListItemResponseList, [])
})
test('MEM-TIME: exclude records outside requested day', options, async (t) => {
  const { call } = await harness(t)
  const result = await call(listPath, { ...query, startTime: '2026-10-02 00:00:00', endTime: '2026-10-02 23:59:59', pageSize: 100 })
  assert.equal(result.total, 11)
  assert.deepEqual(ids(result.memListItemResponseList), Array.from({ length: 11 }, (_, i) => `A-text_mem-${i + 10}`).sort())
})
test('MEM-PROJECTION: large metadata/history omitted, actual body below 200KB', options, async (t) => {
  const { raw } = await harness(t)
  const text = await (await raw(listPath, query)).text()
  assert.ok(Buffer.byteLength(text) < 200000)
  const rows = JSON.parse(text).data.memListItemResponseList
  assert.equal(rows.length, 10)
  const allowed = [
    'memoryId',
    'memoryContent',
    'subjectType',
    'externalUserId',
    'createTime',
    'skillName',
    'skillUrl',
    'profileTemplateId',
    'profileInstanceId',
  ]
  for (const row of rows) assert.ok(Object.keys(row).every((key) => allowed.includes(key)))
})
test('ISOLATION: populated A/B and empty workspace exact memory/log sets', options, async (t) => {
  const db = fixture(3),
    { call } = await harness(t, db)
  for (const projectId of ['A', 'B', 'EMPTY']) {
    const memories = await call(listPath, { ...query, projectId })
    assert.deepEqual(
      ids(memories.memListItemResponseList),
      ids(db.memories.filter((r) => r.projectId === projectId && r.memoryDetailType === 'text_mem')),
    )
    const logs = await call('log/list', { projectId, pageSize: 100 })
    assert.deepEqual(
      ids(logs.logs, 'id'),
      ids(
        db.logs.filter((r) => r.projectId === projectId),
        'id',
      ),
    )
  }
})
test('LOG-PAGE: exact totals and full traversal', options, async (t) => {
  const db = fixture(1),
    { call } = await harness(t, db),
    rows = []
  for (let pageNum = 1; pageNum <= 4; pageNum++) {
    const result = await call('log/list', { projectId: 'A', pageNum, pageSize: 2 })
    assert.equal(result.total, 6)
    assert.equal(result.logs.length, pageNum === 4 ? 0 : 2)
    rows.push(...result.logs)
  }
  assert.deepEqual(
    ids(rows, 'id'),
    ids(
      db.logs.filter((r) => r.projectId === 'A'),
      'id',
    ),
  )
})
test('LOG-FILTER: trace/user/agent/event/status and empty match', options, async (t) => {
  const db = fixture(1),
    { call } = await harness(t, db)
  const scenarios: [Record<string, unknown>, (r: Record<string, unknown>) => boolean][] = [
    [{ query: 'trace-A-1' }, (r) => r.id === 'A-log-1'],
    [{ subjectType: 'user', subjectId: 'u1' }, (r) => r.userId === 'u1'],
    [{ subjectType: 'agent', subjectId: 'a0' }, (r) => r.agentId === 'a0'],
    [{ eventType: 'searchMemory', status: 'FAILED' }, (r) => r.eventType === 'searchMemory' && r.status === 'FAILED'],
    [{ query: 'no-match' }, () => false],
  ]
  for (const [filter, predicate] of scenarios) {
    const expected = db.logs.filter((r) => r.projectId === 'A' && predicate(r))
    const result = await call('log/list', { projectId: 'A', pageSize: 100, ...filter })
    assert.equal(result.total, expected.length)
    assert.deepEqual(ids(result.logs, 'id'), ids(expected, 'id'))
  }
})
test('LOG-TIME: exclude outside-day rows', options, async (t) => {
  const { call } = await harness(t)
  const result = await call('log/list', { projectId: 'A', startTime: '2026-10-02 00:00:00', endTime: '2026-10-02 23:59:59' })
  assert.equal(result.total, 3)
  assert.deepEqual(ids(result.logs, 'id'), ['A-log-3', 'A-log-4', 'A-log-5'])
})
test('LOG-DETAIL: compare fields and JSON bodies to independent fixture', options, async (t) => {
  const db = fixture(1),
    { call } = await harness(t, db),
    expected = db.logs[0]
  const result = await call('log/detail', { logId: expected.id })
  for (const key of ['id', 'createTime', 'eventType', 'status', 'duration']) assert.equal(result[key], expected[key])
  assert.deepEqual(JSON.parse(result.requestBody!), { marker: 0 })
  assert.deepEqual(JSON.parse(result.responseBody!), { ok: true })
})
test('USAGE: exact project/day calls, empty days, zero tokens', options, async (t) => {
  const db = fixture(1),
    { call } = await harness(t, db)
  for (const projectId of ['A', 'B', 'EMPTY', '']) {
    const result = await call('usage/query', { projectId, startDate: '2026-09-30', endDate: '2026-10-03', includeDailyDetails: true })
    assert.deepEqual(
      result.dailyStats.map((r) => r.date),
      ['2026-09-30', '2026-10-01', '2026-10-02', '2026-10-03'],
    )
    for (const day of result.dailyStats) {
      for (const eventType of ['addMessage', 'searchMemory'] as const) {
        const expected = db.logs.filter(
          (r) => (!projectId || r.projectId === projectId) && (r.createTime as string).startsWith(day.date) && r.eventType === eventType,
        ).length
        assert.equal(day[eventType].calls, expected)
        assert.equal(day[eventType].totalTokens, 0)
      }
      for (const kind of ['chat', 'extractMemory'] as const)
        for (const field of ['requestTokens', 'responseTokens', 'totalTokens'] as const) assert.equal(day[kind]![field], 0)
    }
  }
})
test('DELETE: batch delete exact set, isolation and restart persistence', options, async (t) => {
  const h = await harness(t, fixture(3))
  await h.call('project/memory/delete', { memory_ids: ['A-text_mem-0', 'A-text_mem-2'], memType: 'text_mem' })
  await h.restart()
  const result = await h.call(listPath, query)
  assert.equal(result.total, 1)
  assert.deepEqual(ids(result.memListItemResponseList), ['A-text_mem-1'])
  assert.equal((await h.call(listPath, { ...query, projectId: 'B' })).total, 3)
})
test('PROJECT: persisted edit and duplicate name rejection', options, async (t) => {
  const h = await harness(t, fixture(1))
  await h.call('project/update', { id: 'A', name: 'renamed', description: '中文', isMultiView: 1 })
  await h.restart()
  const project = (await h.call('project/list', { pageSize: 10 })).records.find((r) => r.id === 'A')!
  assert.equal(project.name, 'renamed')
  assert.equal(project.description, '中文')
  assert.equal(project.isMultiView, 1)
  assert.notEqual((await (await h.raw('project/create', { name: 'renamed', description: '', isMultiView: 0 })).json()).code, 0)
})
for (const [name, path, body] of [
  ['missing-required', 'project/create', {}],
  ['wrong-type', 'project/create', { name: 123, description: '', isMultiView: 0 }],
  ['invalid-enum', listPath, { ...query, memoryDetailType: 'unknown-type' }],
  ['malformed-json', listPath, '{broken'],
  ['unknown-memory', 'project/memory/detail', { memoryId: 'missing', memType: 'text_mem' }],
  ['unknown-log', 'log/detail', { logId: 'missing' }],
] satisfies [string, string, unknown][]) {
  test(`INPUT-${name}: reject invalid request`, options, async (t) => {
    const { raw } = await harness(t, fixture(1))
    const result = await (await raw(path, body)).json()
    assert.equal(typeof result.code, 'number')
    assert.notEqual(result.code, 0)
    assert.equal(typeof result.message, 'string')
    assert.ok(result.message.length > 0)
  })
}
test('DELETE-REPEAT: duplicate/missing IDs cannot remove unrelated records', options, async (t) => {
  const h = await harness(t, fixture(3))
  await h.call('project/memory/delete', { memory_ids: ['A-text_mem-0'], memType: 'text_mem' })
  for (const memory_ids of [[], ['A-text_mem-0'], ['A-text_mem-0', 'A-text_mem-0', 'missing']]) {
    const result = await (await h.raw('project/memory/delete', { memory_ids, memType: 'text_mem' })).json()
    assert.equal(typeof result.code, 'number')
    assert.deepEqual(ids((await h.call(listPath, query)).memListItemResponseList), ['A-text_mem-1', 'A-text_mem-2'])
  }
})
test('KEY-ISOLATION: keys persist, list/copy agree, other project excluded', options, async (t) => {
  const h = await harness(t, fixture(1))
  const a = await h.call('keys/create', { projectId: 'A', keyName: 'a' })
  const b = await h.call('keys/create', { projectId: 'B', keyName: 'b' })
  await h.restart()
  const list = await h.call('keys/list', { projectId: 'A', pageSize: 100 })
  assert.ok(list.records.some((r) => r.id === a.id && r.apiKey === a.apiKey))
  assert.ok(!list.records.some((r) => r.id === b.id))
  assert.equal(await h.call('keys/copy', { apiKeyId: a.id }), a.apiKey)
})
test('TEMPLATE-FIELDS: nested fields persist and lists are project-scoped', options, async (t) => {
  const h = await harness(t, fixture(1))
  const group = { preference: { value: '中文', algorithm_updatable: false } }
  const a = await h.call('profileTemplate/create', { projectId: 'A', templateName: 'a', group })
  const b = await h.call('profileTemplate/create', { projectId: 'B', templateName: 'b' })
  await h.restart()
  const detail = await h.call(`profileTemplate/detail?projectId=A&templateId=${a.templateId}`, undefined, 'GET')
  assert.deepEqual(detail.group, group)
  assert.ok(!(await h.call('profileTemplate/list', { projectId: 'A', pageSize: 100 })).list.some((r) => r.templateId === b.templateId))
})
test('PROMPT-ISOLATION: independent saved configurations survive restart', options, async (t) => {
  const h = await harness(t, fixture(1))
  for (const projectId of ['A', 'B']) await h.call('prompt-config/save', { projectId, prompt: `prompt-${projectId}` })
  await h.restart()
  for (const projectId of ['A', 'B']) assert.equal((await h.call('prompt-config/detail', { projectId })).prompt, `prompt-${projectId}`)
})
for (const name of [
  'LIVE: upstream write/list/detail/delete and activated counts',
  'AUDIT: JSONL append/rotation/partial rows/missing fields and stable IDs',
  'PERF: 10000 JSONL rows cold/warm first page below 1 second',
  'FAULT: upstream disconnect/timeout/non-JSON and rendered error state',
  'UI: eleven rendered pages and loading/empty/error states',
])
  test.todo(name)
