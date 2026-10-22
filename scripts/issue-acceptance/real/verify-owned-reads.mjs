// Independent native REST oracle; no mutations and no fixture creation.
import assert from 'node:assert/strict'
import { readFile, writeFile } from 'node:fs/promises'
const m = JSON.parse(await readFile(process.env.ISSUE_WORKSPACE_MANIFEST, 'utf8'))
assert.equal(m.state, 'prepared')
const report = { kind: 'native-owned-read-acceptance', status: 'FAIL', memoryWrites: 0, checks: [] }
async function call(base, path, body) {
  const r = await fetch(base + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(15000),
  })
  assert.equal(r.status, 200)
  const j = await r.json()
  assert.ok([0, 200].includes(j.code))
  return j.data
}
const bff = (path, body) => call(m.bff, '/api/dashboard/' + path, body)
const ids = (rows, key = 'id') => rows.map((r) => r[key]).sort()
try {
  for (const project of m.projects) {
    const native = await call(m.upstream, '/product/get_memory', {
      user_id: m.owner,
      mem_cube_id: project.id,
      page: null,
      page_size: null,
      include_preference: true,
      include_tool_memory: true,
      include_skill_memory: true,
    })
    const owned = [...project.memories, ...(project.label === 'A' ? m.sdkSeeds : [])]
    const bucket = (type) =>
      native[type]
        .filter((g) => g.cube_id === project.id)
        .flatMap((g) => g.memories)
        .filter((r) => (r.metadata?.status ?? 'activated') === 'activated')
    const text = bucket('text_mem')
    for (const type of ['text_mem', 'pref_mem', 'event_mem', 'profile_mem', 'skill_mem', 'tool_mem']) {
      const expected = ['event_mem', 'profile_mem'].includes(type)
        ? text.filter((r) => owned.some((o) => o.id === r.id && o.type === type))
        : bucket(type)
      assert.ok(
        expected.every((r) => owned.some((o) => o.id === r.id && o.memory === r.memory)),
        'Unowned or changed native record',
      )
      const query = { projectId: project.id, memoryDetailType: type, pageNum: 1, pageSize: 100 }
      const page = await bff('project/memory/list', query)
      assert.deepEqual(ids(page.memListItemResponseList, 'memoryId'), ids(expected))
      assert.equal(page.total, expected.length)
      assert.ok(Buffer.byteLength(JSON.stringify(page)) < 200000)
      for (const row of page.memListItemResponseList) {
        const source = expected.find((r) => r.id === row.memoryId)
        assert.equal(row.memoryContent, source.memory)
        assert.ok(!row.metadata?.embedding && !row.embedding && !row.history)
        const detail = await bff('project/memory/detail', { memoryId: row.memoryId, memType: type })
        assert.equal(detail.memoryContent, source.memory)
      }
    }
    const query = { projectId: project.id, memoryDetailType: 'text_mem', pageSize: 1 }
    const pages = []
    for (let pageNum = 1; pageNum <= text.length + 1; pageNum++) {
      const page = await bff('project/memory/list', { ...query, pageNum })
      assert.equal(page.total, text.length)
      assert.equal(page.memListItemResponseList.length, pageNum <= text.length ? 1 : 0)
      pages.push(...page.memListItemResponseList)
    }
    assert.deepEqual(ids(pages, 'memoryId'), ids(text))
    assert.equal(new Set(ids(pages, 'memoryId')).size, text.length)
    if (text.length) {
      const marker = text[0].memory
      const expected = text.filter((r) => r.memory.toLowerCase().includes(marker.toLowerCase()))
      const found = await bff('project/memory/list', { ...query, pageSize: 100, memSearchContent: marker.toUpperCase() })
      assert.deepEqual(ids(found.memListItemResponseList, 'memoryId'), ids(expected))
    }
    for (const extra of [
      { externalUserId: m.run + '-absent' },
      { memSearchContent: m.run + '-no-match' },
      { startTime: '2099-01-01T00:00:00Z', endTime: '2099-01-02T00:00:00Z' },
    ])
      assert.equal((await bff('project/memory/list', { ...query, ...extra })).total, 0)
    report.checks.push({
      workspace: project.label,
      sixTypes: true,
      nativeContentAndDetails: true,
      exactPagination: true,
      searchAndFilters: true,
    })
  }
  report.status = 'PASS'
} catch (error) {
  report.failure = {
    type: error.name,
    message: error.name === 'AssertionError' ? error.message.slice(0, 250) : 'Native read acceptance interrupted',
  }
}
await writeFile(process.env.ISSUE_READ_REPORT, JSON.stringify(report, null, 2))
console.log(JSON.stringify(report))
process.exitCode = report.status === 'PASS' ? 0 : 1
