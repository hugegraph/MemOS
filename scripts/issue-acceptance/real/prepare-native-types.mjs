// User-approved four-record native SDK fixture preparation; no production API added.
import assert from 'node:assert/strict'
import { readFile, writeFile, rename } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { remoteProfile } from '../remote-profile.mjs'
assert.ok(process.argv.includes('--allow-write'))
assert.equal(process.env.ISSUE_NATIVE_SDK, 'ssh-docker')
const profile = remoteProfile()
const file = process.env.ISSUE_WORKSPACE_MANIFEST
const m = JSON.parse(await readFile(file, 'utf8'))
assert.equal(m.state, 'prepared')
assert.notEqual(m.sdkState, 'cleaned')
const a = m.projects.find((p) => p.label === 'A')
assert.equal(a.name, m.run + '-A')
assert.match(a.id, /^proj_[0-9a-f-]{36}$/)
async function persist() {
  await writeFile(file + '.tmp', JSON.stringify(m, null, 2))
  await rename(file + '.tmp', file)
}
if (!m.sdkSeeds) {
  m.sdkSeeds = [
    ['pref_mem', 'PreferenceMemory', 'Preference fixture: prefer concise Chinese responses.'],
    ['tool_mem', 'ToolSchemaMemory', 'Tool schema fixture: get_local_time(zone) returns timezone-local time.'],
    ['skill_mem', 'SkillMemory', 'Skill fixture: format-notes\n1. Read notes.\n2. Produce a concise summary.'],
    ['event_mem', 'WorkingMemory', 'Event fixture: completed an isolated acceptance run.'],
  ].map(([type, nativeType, text]) => ({ id: randomUUID(), type, nativeType, memory: `${m.run} SDK ${text}` }))
  m.sdkState = 'planned'
  await persist() // Ownership ledger exists BEFORE any native mutation; retries reuse UUIDs.
}
const code = Buffer.from(await readFile('deploy/dashboard-bff/memory-extension/seed_native_types.py')).toString('base64')
const input = Buffer.from(JSON.stringify({ run: m.run, user: m.owner, cube: a.id, records: m.sdkSeeds })).toString('base64')
const shell = `set -e
${profile.setup}
printf '%s' '${input}' | base64 -d | docker exec -i ${profile.memos} python -c 'import base64; exec(base64.b64decode("${code}"))'
`
const command = Buffer.from(shell).toString('base64')
let report
try {
  const { stdout } = await promisify(execFile)(
    'ssh',
    ['-T', '-o', 'RemoteCommand=none', '-o', 'BatchMode=yes', profile.target, `echo ${command} | base64 -d | bash`],
    { timeout: 150000, maxBuffer: 5000 },
  )
  report = JSON.parse(stdout.trim())
  assert.equal(report.status, 'PASS')
  const response = await fetch(m.upstream + '/product/get_memory', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_id: m.owner,
      mem_cube_id: a.id,
      include_preference: true,
      include_tool_memory: true,
      include_skill_memory: true,
      page: null,
      page_size: null,
    }),
    signal: AbortSignal.timeout(10000),
  })
  assert.equal(response.status, 200)
  const raw = await response.json()
  assert.equal(raw.code, 200)
  for (const seed of m.sdkSeeds) {
    const bucket = seed.type === 'event_mem' ? 'text_mem' : seed.type
    const rows = raw.data[bucket].filter((g) => g.cube_id === a.id).flatMap((g) => g.memories)
    const record = rows.find((r) => r.id === seed.id)
    assert.ok(record)
    assert.equal(record.memory, seed.memory)
    assert.equal(record.metadata.memory_type, seed.nativeType)
    assert.equal(record.metadata.status, 'activated')
    if (seed.type === 'event_mem') assert.equal(record.metadata.dashboard_type, 'event_mem')
    if (seed.type === 'skill_mem') assert.equal(record.metadata.skill_name, m.run + '-format-notes')
  }
  for (const seed of m.sdkSeeds.filter((r) => r.type === 'event_mem')) if (!a.memories.some((r) => r.id === seed.id)) a.memories.push(seed)
  m.sdkState = 'prepared'
  await persist()
  report.restBucketReadback = 'PASS'
  report.note = 'Native SDK fixtures verify real read mapping, not REST automatic extraction capability'
} catch (error) {
  try {
    report = JSON.parse(error.stdout?.trim())
  } catch {
    report = { status: 'FAIL', failure: { type: error.name } }
  }
}
await writeFile(
  process.env.ISSUE_NATIVE_SDK_REPORT || '.data/issue-acceptance/ui5-native-types-prepare.json',
  JSON.stringify(report, null, 2),
)
console.log(JSON.stringify(report))
process.exitCode = report.status === 'PASS' && m.sdkState === 'prepared' ? 0 : 1
