// Real invalid-request safety probes with independent native graph/vector snapshots.
import assert from 'node:assert/strict'
import { readFile, writeFile } from 'node:fs/promises'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { storageSnapshot } from '../browser/native-delete-oracle.ts'
import { remoteProfile } from '../remote-profile.ts'
import type { WorkspaceManifest } from '../../tool-types.ts'

assert.ok(process.argv.includes('--allow-write'))
const profile = remoteProfile()
const file = process.env.ISSUE_WORKSPACE_MANIFEST
const m: WorkspaceManifest = JSON.parse(await readFile(file!, 'utf8'))
assert.equal(m.state, 'prepared')
const a = m.projects.find((p) => p.label === 'A')!,
  b = m.projects.find((p) => p.label === 'B')!
const removed = [...(m.legacyHardDeleted || []), ...(m.browserDeleted || [])]
const target = a.memories.find((r) => !removed.includes(r.id)),
  other = b.memories[0]
assert.ok(target && other)
const before = [await storageSnapshot(a, target), await storageSnapshot(b, other)]
assert.ok(before.every((s) => s.storedStatus === 'activated' && s.vectorStatus === 'activated'))
const report: { kind: string; status: string; checks: string[]; native?: unknown; failure?: { type: string; message: string } } = {
  kind: 'real-soft-delete-guards',
  status: 'FAIL',
  checks: [],
}
try {
  const response = await fetch(m.bff + '/api/dashboard/project/memory/delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ memory_ids: [target.id, other.id], memType: 'text_mem' }),
    signal: AbortSignal.timeout(15000),
  })
  assert.equal(response.status, 400)
  assert.notEqual((await response.json()).code, 0)
  report.checks.push('BFF rejects cross-authorized-cube batch')
  const code = Buffer.from(await readFile('deploy/dashboard-bff/memory-extension/verify_guards.py')).toString('base64')
  const input = Buffer.from(JSON.stringify({ user: m.owner, cube: a.id, owned: target.id, other: other.id })).toString('base64')
  const shell = `set -e
${profile.setup}
printf '%s' '${input}' | base64 -d | docker exec -i ${profile.memos} python -c 'import base64; exec(base64.b64decode("${code}"))'
`
  const command = Buffer.from(shell).toString('base64')
  const { stdout } = await promisify(execFile)(
    'ssh',
    ['-T', '-o', 'RemoteCommand=none', '-o', 'BatchMode=yes', profile.target, `echo ${command} | base64 -d | bash`],
    { timeout: 65000, maxBuffer: 5000 },
  )
  const native = JSON.parse(stdout.trim())
  assert.equal(native.status, 'PASS')
  report.native = native.checks
  const after = [await storageSnapshot(a, target), await storageSnapshot(b, other)]
  assert.deepEqual(after, before)
  report.checks.push('A/B native node, edge count, vector and payload snapshots unchanged after all invalid requests')
  report.status = 'PASS'
} catch (error) {
  report.failure = { type: (error as Error).name, message: (error as Error).message.slice(0, 300) }
}
await writeFile('.data/issue-acceptance/ui5-real-delete-guards.json', JSON.stringify(report, null, 2))
console.log(JSON.stringify(report))
process.exitCode = report.status === 'PASS' ? 0 : 1
