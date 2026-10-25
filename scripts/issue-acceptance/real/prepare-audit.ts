// Fresh real authenticated MCP invocation + independent native JSONL oracle.
import assert from 'node:assert/strict'
import { readFile, writeFile } from 'node:fs/promises'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { remoteProfile } from '../remote-profile.ts'
import type { WorkspaceManifest } from '../../tool-types.ts'
assert.equal(process.env.ISSUE_NATIVE_DELETE_ORACLE, 'ssh-docker')
const profile = remoteProfile(process.env, { audit: true })
const manifest: WorkspaceManifest = JSON.parse(await readFile(process.env.ISSUE_WORKSPACE_MANIFEST!, 'utf8'))
assert.ok(manifest.protectedUserId && manifest.protectedId)
const identity = Buffer.from(JSON.stringify({ user: manifest.protectedUserId, cube: manifest.protectedId })).toString('base64')
assert.ok(process.env.ISSUE_AUDIT_ORACLE)
const probe = (await readFile('deploy/dashboard-bff/probe-gateway.py')).toString('base64')
const audit = (await readFile('deploy/dashboard-bff/prepare-browser-audit.ts')).toString('base64')
const script = `set -e
${profile.setup}
python3 - <<'PY'
import subprocess,json,base64
identity=json.loads(base64.b64decode('${identity}'))
env=['-e','AUDIT_PROBE_USER='+identity['user'],'-e','AUDIT_PROBE_CUBE='+identity['cube']]
p=subprocess.run(['docker','exec',*env,'${profile.gateway}','python','-c','import base64;exec(base64.b64decode("${probe}"))'],capture_output=True,text=True,timeout=50)
assert p.returncode==0,'Fresh read-only MCP probe failed'
r=json.loads(p.stdout);assert r['status']=='PASS' and r['memory_writes']==0
a=subprocess.run(['docker','exec','-i',*env,'-e','AUDIT_VERIFY_MARKER='+str(r['current']),'${profile.bff}','node','--input-type=module-typescript'],input=base64.b64decode('${audit}').decode(),capture_output=True,text=True,timeout=40)
assert a.returncode==0,'Native audit oracle failed'
out=json.loads(a.stdout);assert out['status']=='PASS';print(json.dumps(out))
PY
`
try {
  const { stdout } = await promisify(execFile)(
    'ssh',
    [
      '-T',
      '-o',
      'RemoteCommand=none',
      '-o',
      'BatchMode=yes',
      profile.target,
      `echo ${Buffer.from(script).toString('base64')} | base64 -d | bash`,
    ],
    { timeout: 100000, maxBuffer: 10000 },
  )
  const report = JSON.parse(stdout)
  assert.equal(report.status, 'PASS')
  assert.equal(report.rawRecordFound, 1)
  await writeFile(process.env.ISSUE_AUDIT_ORACLE, JSON.stringify(report, null, 2))
  console.log('PASS: new authenticated MCP call and native JSONL oracle; zero memory writes')
} catch {
  console.error('FAIL: fresh MCP/native JSONL verification interrupted; no credentials logged')
  process.exitCode = 1
}
