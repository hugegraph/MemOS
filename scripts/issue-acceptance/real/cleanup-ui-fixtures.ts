// Exact-ID cleanup only. Active records use the authenticated Dashboard soft-delete;
// every retained native node/vector is verified before revoking owned BFF mappings.
import assert from 'node:assert/strict'
import { readFile, writeFile, rename } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { storageSnapshot, deletedStatus } from '../browser/native-delete-oracle.ts'
import { remoteProfile } from '../remote-profile.ts'
import type {
  WorkspaceManifest,
  Report,
  NativeBuckets,
  NativeTextMemory,
  DashboardCall,
  DashboardResult,
  Envelope,
} from '../../tool-types.ts'
import type { StorageSnapshot } from '../browser/native-delete-oracle.ts'
assert.ok(process.argv.includes('--allow-write'))
assert.equal(process.env.ISSUE_NATIVE_DELETE_ORACLE, 'ssh-docker')
const profile = remoteProfile()
const file = process.env.ISSUE_WORKSPACE_MANIFEST
const m: WorkspaceManifest = JSON.parse(await readFile(file!, 'utf8'))
assert.equal(m.state, 'prepared')
assert.equal(m.sdkState, 'prepared')
assert.match(m.run, /^batch3-[0-9a-f]{8}$/)
const buckets = ['text_mem', 'pref_mem', 'tool_mem', 'skill_mem']
const report: Report & {
  projects: { label?: string; softDeletedNow: number; allFourBucketsActiveZero: boolean; mappingRevoked: boolean }[]
  retained: StorageSnapshot[]
  legacy?: { status: string }
} = { kind: 'real-owned-ui-fixture-cleanup', status: 'FAIL', projects: [], retained: [] }
async function call<P extends string>(origin: string, path: P, body?: unknown, status = 200): Promise<DashboardResult<P>> {
  const response = await fetch(origin + path, {
    method: body ? 'POST' : 'GET',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(20000),
  })
  assert.equal(response.status, status)
  const envelope = (await response.json()) as Envelope<DashboardResult<P>>
  if (status === 200) assert.ok([0, 200].includes(envelope.code))
  return envelope.data
}
const bff = ((path: string, body?: unknown, status?: number) => call(m.bff, '/api/dashboard/' + path, body, status)) as DashboardCall
const raw = (cube: string, user = m.owner) =>
  call(m.upstream, '/product/get_memory', {
    user_id: user,
    mem_cube_id: cube,
    include_preference: true,
    include_tool_memory: true,
    include_skill_memory: true,
    page: null,
    page_size: null,
  })
const rows = (data: NativeBuckets, bucket: string, cube: string) =>
  data[bucket].filter((g) => g.cube_id === cube).flatMap((g) => g.memories)
const active = (list: NativeTextMemory[]) => list.filter((r) => (r.metadata?.status || 'activated') === 'activated')
const fingerprint = (list: NativeTextMemory[]) =>
  createHash('sha256')
    .update(JSON.stringify(list.map((r) => [r.id, r.memory, r.metadata?.status] as const).sort((a, b) => a[0].localeCompare(b[0]))))
    .digest('hex')
async function protectedCheck() {
  assert.equal(fingerprint(rows(await raw(m.protectedId, m.protectedUserId), 'text_mem', m.protectedId)), m.protectedFingerprint)
}
async function persist() {
  await writeFile(file + '.tmp', JSON.stringify(m, null, 2))
  await rename(file + '.tmp', file!)
}
try {
  const original = m.before.find((p) => p.status === 'ENABLED')
  assert.ok(original)
  const current = await bff('project/dict')
  const selected = current.find((p) => p.status === 'ENABLED')
  assert.ok(
    selected && [original.id, ...m.projects.map((p) => p.id)].includes(selected.id),
    'Do not overwrite an unrelated concurrent selection',
  )
  await protectedCheck()
  // Preflight all namespaces before any cleanup mutation.
  for (const project of m.projects) {
    assert.match(project.id, /^proj_[0-9a-f-]{36}$/)
    assert.ok(project.name.startsWith(m.run + '-') && !m.before.some((p) => p.id === project.id))
    const binding = current.find((p) => p.id === project.id)
    if (binding) {
      assert.equal(binding.name, project.name)
      assert.equal((await bff('keys/list', { projectId: project.id, pageSize: 100 })).total, 0, 'Tracked key cleanup must complete first')
    }
    const owned = [...new Map([...project.memories, ...(project.label === 'A' ? m.sdkSeeds : [])].map((r) => [r.id, r])).values()]
    assert.ok(owned.every((r) => r.memory.includes(m.run)))
    const data = await raw(project.id)
    for (const bucket of buckets)
      for (const record of active(rows(data, bucket, project.id))) {
        const expected = owned.find((r) => r.id === record.id)
        assert.ok(expected, 'Unknown record prevents cleanup/unbinding')
        assert.equal(record.memory, expected.memory)
        assert.equal(record.metadata!.user_id, m.owner)
      }
  }
  if (selected.id !== original.id) await bff('project/update', { id: original.id, status: 'ENABLED' })
  for (const project of m.projects) {
    const owned = [...new Map([...project.memories, ...(project.label === 'A' ? m.sdkSeeds : [])].map((r) => [r.id, r])).values()]
    const oracleProject = { ...project, memories: owned }
    let deletedNow = 0
    for (const bucket of buckets) {
      const remaining = active(rows(await raw(project.id), bucket, project.id))
      if (!remaining.length) continue
      const before = new Map<string, StorageSnapshot>()
      for (const record of remaining)
        before.set(
          record.id,
          await storageSnapshot(
            oracleProject,
            owned.find((r) => r.id === record.id)!,
          ),
        )
      await bff('project/memory/delete', { memory_ids: remaining.map((r) => r.id), memType: bucket })
      for (const record of remaining) {
        const after = await deletedStatus(
          oracleProject,
          owned.find((r) => r.id === record.id)!,
        )
        for (const field of ['graphFingerprint', 'vectorFingerprint', 'payloadFingerprint', 'edges'] as const)
          assert.equal(after[field], before.get(record.id)![field])
      }
      deletedNow += remaining.length
    }
    const after = await raw(project.id)
    for (const bucket of buckets) assert.equal(active(rows(after, bucket, project.id)).length, 0)
    const legacy = owned.filter((r) => (m.legacyHardDeleted || []).includes(r.id))
    if (legacy.length) {
      const code = Buffer.from(await readFile('deploy/dashboard-bff/memory-extension/reconcile_legacy_vector.py')).toString('base64')
      const payload = Buffer.from(
        JSON.stringify({
          run: m.run,
          cube: project.id,
          user: m.owner,
          records: legacy.map((r) => ({ id: r.id, memoryHash: createHash('sha256').update(r.memory).digest('hex') })),
        }),
      ).toString('base64')
      const shell = `set -e\n${profile.setup}\nprintf '%s' '${payload}' | base64 -d | docker exec -i ${profile.memos} python -c 'import base64; exec(base64.b64decode("${code}"))'\n`
      const { stdout } = await promisify(execFile)(
        'ssh',
        [
          '-T',
          '-o',
          'RemoteCommand=none',
          '-o',
          'BatchMode=yes',
          profile.target,
          `echo ${Buffer.from(shell).toString('base64')} | base64 -d | bash`,
        ],
        { timeout: 30000, maxBuffer: 5000 },
      )
      report.legacy = JSON.parse(stdout.trim())
      assert.equal(report.legacy!.status, 'PASS')
    }
    for (const record of owned.filter((r) => !legacy.some((l) => l.id === r.id)))
      report.retained.push(await deletedStatus(oracleProject, record))
    if ((await bff('project/dict')).some((p) => p.id === project.id)) await bff('project/revoke/' + project.id, {})
    await bff('project/memory/list', { projectId: project.id, memoryDetailType: 'text_mem' }, 404)
    report.projects.push({ label: project.label, softDeletedNow: deletedNow, allFourBucketsActiveZero: true, mappingRevoked: true })
    await writeFile('.data/issue-acceptance/ui5-cleanup-progress.json', JSON.stringify(report, null, 2))
  }
  assert.equal((await bff('project/dict')).find((p) => p.status === 'ENABLED')?.id, original.id)
  await protectedCheck()
  m.state = 'cleaned'
  m.sdkState = 'cleaned'
  await persist()
  report.status = 'PASS'
  report.originalSelectionAndProtectedDataPreserved = true
  report.note =
    'Native cube registrations and soft-deleted records retained. Historical hard-deleted node remains a separately recorded failure; orphan vector only marked deleted.'
} catch (error) {
  report.failure = {
    type: (error as Error).name,
    message:
      (error as Error).name === 'AssertionError'
        ? (error as Error).message.slice(0, 300)
        : 'Scoped cleanup interrupted; retain manifest and resume safely',
  }
}
await writeFile(process.env.ISSUE_CLEANUP_REPORT || '.data/issue-acceptance/ui5-cleanup.json', JSON.stringify(report, null, 2))
console.log(JSON.stringify({ ...report, retained: report.retained.length }))
process.exitCode = report.status === 'PASS' ? 0 : 1
