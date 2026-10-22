// Executes real drivers, never trusts a supplied PASS file as a completed test.
// Preparation is explicit and separate; this command always cleans prepared owned fixtures.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { resolve, join } from 'node:path'
import { remoteProfile } from '../remote-profile.mjs'

const args = process.argv.slice(2)
const value = (key) => args[args.indexOf(key) + 1]
const report = { kind: 'real-core-functional-acceptance', status: 'BLOCKED', phases: [] }
let output,
  env,
  prepared = false
async function run(script, extra = []) {
  const child = spawn(process.execPath, [script, ...extra], { env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true })
  let log = ''
  for (const stream of [child.stdout, child.stderr])
    stream.on('data', (data) => {
      log = (log + data.toString()).slice(-1000000)
    })
  const code = await new Promise((ok, fail) => {
    child.once('error', fail)
    child.once('close', ok)
  })
  await writeFile(join(output, script.split('/').at(-1) + '.log'), log)
  return code
}
try {
  if (
    !args.includes('--allow-write') ||
    !args.includes('--allow-service-stop') ||
    !args.includes('--profile') ||
    value('--profile') !== 'ssh-docker' ||
    !args.includes('--manifest')
  ) {
    report.reason =
      'Requires --profile ssh-docker --manifest PREPARED_MANIFEST --allow-write --allow-service-stop; no network or mutations performed'
  } else {
    const profile = remoteProfile(process.env, { lifecycle: true, audit: true })
    const manifest = resolve(value('--manifest'))
    const m = JSON.parse(await readFile(manifest, 'utf8'))
    assert.equal(m.state, 'prepared')
    assert.equal(m.sdkState, 'prepared')
    assert.ok(typeof m.owner === 'string' && m.owner.trim())
    assert.ok(typeof m.protectedId === 'string' && m.protectedId.trim())
    assert.equal(
      m.before.find((p) => p.status === 'ENABLED')?.id,
      m.protectedId,
      'The profile requires the protected workspace as the saved initial selection',
    )
    assert.match(m.run, /^batch3-[0-9a-f]{8}$/)
    for (const url of [m.bff, m.upstream]) assert.equal(new URL(url).hostname, '127.0.0.1')
    output = resolve('.data/issue-acceptance/real-' + Date.now())
    await mkdir(output)
    env = {
      ...process.env,
      ISSUE_CASES: '',
      ISSUE_API_BASE: m.bff,
      ISSUE_UPSTREAM_BASE: m.upstream,
      ISSUE_WORKSPACE_MANIFEST: manifest,
      ISSUE_AUDIT_ORACLE: join(output, 'audit.json'),
      ISSUE_NATIVE_DELETE_ORACLE: 'ssh-docker',
      ISSUE_ALLOW_STOP_UPSTREAM: profile.memos,
      ISSUE_REPORT_DIR: output,
      ISSUE_READ_REPORT: join(output, 'native-reads.json'),
      ISSUE_CLEANUP_REPORT: join(output, 'cleanup.json'),
      ISSUE_LOG_FIXTURE: '',
      ISSUE_LOG_MISSING_FIELDS: '',
      ISSUE_MOCK_FIXTURE: '',
    }
    assert.ok(env.ISSUE_CHROME, 'Select installed browser with ISSUE_CHROME')
    prepared = true
    assert.equal(await run('scripts/issue-acceptance/verify-frontend-unchanged.mjs'), 0)
    assert.equal(await run('scripts/issue-acceptance/real/verify-owned-reads.mjs'), 0)
    report.phases.push({ id: 'native-reads', status: 'PASS' })
    assert.equal(await run('scripts/issue-acceptance/real/prepare-audit.mjs'), 0)
    report.phases.push({ id: 'fresh-gateway-jsonl', status: 'PASS' })
    // Preparation intentionally selects B to exercise persistence. General pages
    // must start from the saved demo, not ambiguous same-tenant fixture workspaces.
    const selected = await fetch(m.bff + '/api/dashboard/project/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: m.protectedId, status: 'ENABLED' }),
      signal: AbortSignal.timeout(15000),
    })
    assert.equal(selected.status, 200)
    assert.equal((await selected.json()).code, 0)
    const code = await run('scripts/issue-acceptance/browser/production-run.mjs', ['--allow-write', '--allow-service-stop'])
    const browser = JSON.parse(await readFile(join(output, 'browser.json'), 'utf8'))
    assert.equal(browser.selectedCases, null, 'Must execute the full plan')
    assert.equal(code, browser.overallStatus === 'PASS' ? 0 : browser.overallStatus === 'BLOCKED' ? 2 : 1)
    report.phases.push({ id: 'production-browser', status: browser.overallStatus })
    report.status = browser.overallStatus
    report.nonPassing = browser.rows.filter((r) => r.status !== 'PASS').map(({ id, status, reason }) => ({ id, status, reason }))
    report.fixtureRun = m.run
    assert.equal(await run('scripts/issue-acceptance/browser/missing-fields.mjs'), 0)
    report.phases.push({ id: 'missing-jsonl-fields-ui', status: 'PASS' })
  }
} catch (error) {
  report.status = 'FAIL'
  report.reason = error.name === 'AssertionError' ? error.message.slice(0, 250) : 'Driver interrupted; inspect scoped logs'
} finally {
  if (prepared) {
    try {
      const code = await run('scripts/issue-acceptance/real/cleanup-ui-fixtures.mjs', ['--allow-write'])
      const cleanup = JSON.parse(await readFile(env.ISSUE_CLEANUP_REPORT, 'utf8'))
      assert.equal(code, 0)
      assert.equal(cleanup.status, 'PASS')
      report.phases.push({ id: 'owned-cleanup', status: 'PASS' })
    } catch {
      report.status = 'FAIL'
      report.phases.push({
        id: 'owned-cleanup',
        status: 'FAIL',
        reason: 'Retain manifest and recover scoped cleanup; never delete broadly',
      })
    }
  }
  if (output) {
    report.output = output
    await writeFile(join(output, 'real.json'), JSON.stringify(report, null, 2))
  }
}
console.log(JSON.stringify(report, null, 2))
process.exitCode = report.status === 'PASS' ? 0 : report.status === 'BLOCKED' ? 2 : 1
