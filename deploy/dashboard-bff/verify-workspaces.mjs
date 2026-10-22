// Explicit opt-in, staged real REST acceptance. Creates only run-owned projects; never deletes pre-existing memories.
// prepare -> restart ONLY the dedicated BFF -> verify -> cleanup. Persist manifest after each write.
import assert from 'node:assert/strict'
import { readFile, writeFile, rename, access } from 'node:fs/promises'
import { randomUUID, createHash } from 'node:crypto'

const phase = process.argv[2]
const file = process.env.WORKSPACE_VERIFY_MANIFEST
let manifest,
  stage = 'configuration'
const report = { phase, status: 'FAIL', checks: [] }
const deadline = Date.now() + 180000
let calls = 0
const tabs = ['text_mem', 'pref_mem', 'tool_mem', 'skill_mem', 'event_mem', 'profile_mem']
const active = (rows) => rows.filter((r) => (r.metadata?.status ?? 'activated') === 'activated')
const ids = (rows) => rows.map((r) => r.id).sort()
const fingerprint = (rows) =>
  createHash('sha256')
    .update(JSON.stringify(rows.map((r) => [r.id, r.memory, r.metadata?.status]).sort((a, b) => a[0].localeCompare(b[0]))))
    .digest('hex')
async function persist() {
  await writeFile(file + '.tmp', JSON.stringify(manifest, null, 2), { mode: 0o600 })
  await rename(file + '.tmp', file)
}
async function request(base, path, body, expected = 200) {
  assert.ok(++calls <= 100 && Date.now() < deadline, 'Acceptance request/time budget exceeded')
  const response = await fetch(base + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(Math.max(1, Math.min(90000, deadline - Date.now()))),
    redirect: 'error',
  })
  assert.equal(response.status, expected, `HTTP ${path}`)
  const envelope = await response.json()
  if (expected !== 200) {
    assert.notEqual(envelope.code, 0)
    return
  }
  assert.ok([0, 200].includes(envelope.code), `Business error ${path}`)
  return envelope.data
}
const bff = (path, body, expected) => request(manifest.bff, '/api/dashboard/' + path, body, expected)
async function raw(projectId, bucket = 'text_mem') {
  const data = await request(manifest.upstream, '/product/get_memory', {
    user_id: projectId === manifest.protectedId ? manifest.protectedUserId : manifest.owner,
    mem_cube_id: projectId,
    page: null,
    page_size: null,
    include_preference: true,
    include_tool_memory: true,
    include_skill_memory: true,
  })
  assert.ok(Array.isArray(data[bucket]))
  const rows = data[bucket].filter((group) => group.cube_id === projectId).flatMap((group) => group.memories)
  assert.ok(rows.length <= 50, 'Use bounded dedicated fixtures')
  return rows
}
async function list(projectId, tab = 'text_mem', extra = {}) {
  return bff('project/memory/list', { projectId, memoryDetailType: tab, pageSize: 100, ...extra })
}
function owned(project) {
  assert.ok(project && /^proj_[0-9a-f-]{36}$/.test(project.id) && project.name.startsWith(manifest.run + '-'))
  assert.ok(!manifest.before.some((p) => p.id === project.id))
}
async function reconcile(project) {
  owned(project)
  const rows = await raw(project.id)
  for (const row of rows) {
    assert.ok(typeof row.memory === 'string' && row.memory.includes(manifest.run), 'Refuse to adopt an unowned memory')
    if (!project.memories.some((m) => m.id === row.id))
      project.memories.push({ id: row.id, memory: row.memory, type: row.metadata?.dashboard_type ?? row.metadata?.type ?? null })
  }
  await persist()
  return rows
}

try {
  assert.ok(['prepare', 'verify', 'cleanup'].includes(phase) && process.argv.includes('--allow-write') && file)
  if (phase === 'prepare') {
    let exists = false
    try {
      await access(file)
      exists = true
    } catch {
      /* No prior manifest: this is a new run. */
    }
    assert.equal(exists, false, 'Do not overwrite a previous ownership manifest')
    const bffURL = new URL(process.env.WORKSPACE_VERIFY_BFF),
      upstreamURL = new URL(process.env.WORKSPACE_VERIFY_UPSTREAM)
    for (const url of [bffURL, upstreamURL]) assert.ok(['http:', 'https:'].includes(url.protocol) && !url.username && !url.password)
    assert.ok(process.env.WORKSPACE_VERIFY_OWNER)
    manifest = {
      version: 2,
      run: `batch3-${randomUUID().slice(0, 8)}`,
      bff: bffURL.origin,
      upstream: upstreamURL.origin,
      owner: process.env.WORKSPACE_VERIFY_OWNER,
      projects: [],
      state: 'preparing',
    }
    manifest.before = await bff('project/dict')
    // This deployment's existing demo is only fingerprinted, never changed or used as a write target.
    manifest.protectedId = process.env.WORKSPACE_VERIFY_PROTECTED_PROJECT
    manifest.protectedUserId = process.env.WORKSPACE_VERIFY_PROTECTED_USER_ID
    assert.ok(manifest.protectedId && manifest.before.some((p) => p.id === manifest.protectedId))
    assert.ok(manifest.protectedUserId)
    const protectedRows = await raw(manifest.protectedId)
    assert.ok(protectedRows.length > 0, 'Protected baseline must be read with the correct user identity')
    manifest.protectedFingerprint = fingerprint(protectedRows)
    await persist()
    for (const label of ['A', 'B', 'empty']) {
      stage = 'create-' + label
      const project = await bff('project/create', { name: `${manifest.run}-${label}`, description: 'Dedicated batch3 acceptance fixture' })
      owned(project)
      manifest.projects.push({ id: project.id, name: project.name, label, memories: [] })
      await persist()
      assert.equal((await raw(project.id)).length, 0, 'New cube must start empty')
    }
    for (const project of manifest.projects) {
      const types = project.label === 'A' ? ['text_mem', 'event_mem', 'profile_mem'] : project.label === 'B' ? ['text_mem'] : []
      for (const type of types) {
        stage = 'write-' + project.label + '-' + type
        owned(project)
        const content = `${manifest.run} ${project.label} ${type} dedicated acceptance content`
        await request(manifest.upstream, '/product/add', {
          user_id: manifest.owner,
          writable_cube_ids: [project.id],
          async_mode: 'sync',
          mode: 'fast',
          messages: [{ role: 'user', content }],
          info: { dashboard_type: type, batch3_run: manifest.run },
        })
        const rows = active(await reconcile(project))
        assert.ok(
          rows.some((r) => r.memory.includes(content) && r.metadata?.dashboard_type === type),
          'REST must preserve the explicit metadata.dashboard_type marker',
        )
      }
    }
    await bff('project/update', { id: manifest.projects[1].id, status: 'ENABLED' })
    manifest.state = 'prepared'
    await persist()
    report.checks.push({
      name: 'real project creation and REST fast writes',
      projects: 3,
      memoryRecords: manifest.projects.reduce((n, p) => n + p.memories.length, 0),
    })
  } else {
    manifest = JSON.parse(await readFile(file, 'utf8'))
    assert.equal(manifest.version, 2)
    assert.ok(/^batch3-[0-9a-f]{8}$/.test(manifest.run))
    if (phase === 'verify') assert.equal(manifest.projects.length, 3)
    else assert.ok(manifest.projects.length <= 3) // Also recover an interrupted prepare.
    for (const p of manifest.projects) owned(p)
    if (phase === 'verify') {
      assert.equal(manifest.state, 'prepared')
      stage = 'restart-persistence'
      const dict = await bff('project/dict')
      for (const p of manifest.projects) assert.ok(dict.some((d) => d.id === p.id && d.name === p.name))
      assert.equal(dict.find((p) => p.id === manifest.projects[1].id).status, 'ENABLED')
      const [a, b, empty] = manifest.projects
      for (const project of manifest.projects) {
        stage = 'switch-and-scope-' + project.label
        await bff('project/update', { id: project.id, status: 'ENABLED' })
        assert.deepEqual(
          (await bff('project/dict')).filter((p) => p.status === 'ENABLED').map((p) => p.id),
          [project.id],
        )
        const upstream = active(await raw(project.id))
        const page = await list(project.id)
        assert.deepEqual(page.memListItemResponseList.map((r) => r.memoryId).sort(), ids(upstream))
        assert.equal(page.total, project.memories.length)
        for (const tab of tabs) {
          const expected =
            tab === 'text_mem'
              ? ids(upstream)
              : ['event_mem', 'profile_mem'].includes(tab)
                ? ids(upstream.filter((r) => (r.metadata?.dashboard_type ?? r.metadata?.type) === tab))
                : ids(active(await raw(project.id, tab)))
          const view = await list(project.id, tab)
          assert.deepEqual(view.memListItemResponseList.map((r) => r.memoryId).sort(), expected)
          assert.equal(view.total, expected.length)
          assert.ok(Buffer.byteLength(JSON.stringify(view)) < 200000)
          for (const item of view.memListItemResponseList) {
            const detail = await bff('project/memory/detail', { memoryId: item.memoryId, memType: tab })
            const original = upstream.find((r) => r.id === item.memoryId)
            if (original) assert.equal(detail.memoryContent, original.memory)
          }
        }
      }
      stage = 'paging-and-filter'
      const first = await list(a.id, 'text_mem', { pageSize: 1, pageNum: 1 }),
        second = await list(a.id, 'text_mem', { pageSize: 1, pageNum: 2 })
      assert.notEqual(first.memListItemResponseList[0].memoryId, second.memListItemResponseList[0].memoryId)
      assert.equal(
        (
          await list(a.id, 'text_mem', {
            memSearchContent: `${manifest.run} A event_mem`,
            externalUserId: manifest.owner,
            subjectType: 'user',
          })
        ).total,
        1,
      )
      assert.equal((await list(empty.id)).total, 0)
      const event = a.memories.find((m) => m.type === 'event_mem'),
        profile = a.memories.find((m) => m.type === 'profile_mem')
      stage = 'wrong-tab-rejected-before-delete'
      await bff('project/memory/delete', { memory_ids: [event.id, profile.id], memType: 'event_mem' }, 404)
      assert.equal((await list(a.id)).total, a.memories.length)
      stage = 'real-soft-delete'
      await bff('project/memory/delete', { memory_ids: [event.id], memType: 'event_mem' })
      assert.ok(!active(await raw(a.id)).some((r) => r.id === event.id))
      await bff('project/memory/detail', { memoryId: event.id, memType: 'event_mem' }, 404)
      assert.equal((await list(b.id)).total, b.memories.length)
      assert.equal(fingerprint(await raw(manifest.protectedId)), manifest.protectedFingerprint)
      manifest.state = 'verified'
      await persist()
      report.checks.push({ name: 'restart/selection/isolation/six views/paging/filter/soft-delete', status: 'PASS' })
      report.note =
        'Positive real records cover text/event/profile. Preference/tool/skill are verified empty, not positive-data acceptance.'
    } else {
      stage = 'owned-record-cleanup'
      for (const project of manifest.projects) {
        const current = await bff('project/dict')
        if (!current.some((p) => p.id === project.id)) continue // Safe resume after an interrupted cleanup.
        assert.equal(current.find((p) => p.id === project.id).name, project.name)
        const remaining = active(await reconcile(project))
        if (remaining.length) await bff('project/memory/delete', { memory_ids: ids(remaining), memType: 'text_mem' })
        assert.equal(active(await raw(project.id)).length, 0)
        await bff(`project/revoke/${project.id}`, {})
        await bff('project/memory/list', { projectId: project.id, memoryDetailType: 'text_mem' }, 404)
      }
      const selected = manifest.before.find((p) => p.status === 'ENABLED')
      if (selected) await bff('project/update', { id: selected.id, status: 'ENABLED' })
      assert.deepEqual((await bff('project/dict')).map((p) => p.id).sort(), manifest.before.map((p) => p.id).sort())
      assert.equal(fingerprint(await raw(manifest.protectedId)), manifest.protectedFingerprint)
      manifest.state = 'cleaned'
      await persist()
      report.checks.push({ name: 'owned records soft-deleted, test mappings revoked, original selection/data preserved', status: 'PASS' })
      report.note = 'Empty upstream cube registration records remain: Dashboard revoke is unbinding, not physical cube destruction.'
    }
  }
  report.status = 'PASS'
} catch (error) {
  report.failure = { stage, type: error.name }
  if (manifest && file) await persist().catch(() => {})
}
report.calls = calls
console.log(JSON.stringify(report, null, 2))
process.exitCode = report.status === 'PASS' ? 0 : 1
