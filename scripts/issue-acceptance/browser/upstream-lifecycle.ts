// Explicit task-only stop. Remote EXIT trap and 60s watchdog restore the service
// even if the browser/parent fails or SSH stdin disappears. No DB container touched.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { remoteProfile } from '../remote-profile.ts'

export async function withUpstreamStopped<T>(operation: () => Promise<T>) {
  const profile = remoteProfile(process.env, { lifecycle: true })
  assert.equal(process.env.ISSUE_ALLOW_STOP_UPSTREAM, profile.memos)
  assert.ok(process.argv.includes('--allow-service-stop'))
  const script = `set -e
${profile.setup}
name=${profile.memos}
test "$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$name")" = '${profile.project}'
test "$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.service" }}' "$name")" = '${profile.service}'
test "$(docker inspect -f '{{.Image}}' "$name")" = '${profile.imageId}'
test "$(docker inspect -f '{{.State.Running}}' "$name")" = true
restore() {
  trap - EXIT HUP INT TERM
  if ! docker start "$name" >/dev/null; then echo RESTORE_FAILED; return 1; fi
  for n in $(seq 1 60); do
    if docker exec "$name" python -c 'import urllib.request; assert urllib.request.urlopen("http://127.0.0.1:8000/health",timeout=3).status==200' >/dev/null 2>&1; then echo RESTORED; return 0; fi
    sleep 2
  done
  echo RESTORE_FAILED
  return 1
}
trap restore EXIT
trap 'restore; exit 1' HUP INT TERM
docker stop --time 10 "$name" >/dev/null
test "$(docker inspect -f '{{.State.Running}}' "$name")" = false
echo STOPPED
read -r -t 60 release || true
`
  const encoded = Buffer.from(script).toString('base64')
  const child = spawn(
    'ssh',
    ['-T', '-o', 'RemoteCommand=none', '-o', 'BatchMode=yes', profile.target, `bash -c "$(printf '%s' '${encoded}' | base64 -d)"`],
    { stdio: ['pipe', 'pipe', 'pipe'] },
  )
  let output = '',
    errors = ''
  const done = new Promise((resolve) => child.once('close', (code) => resolve(code)))
  child.stderr.on('data', (data) => {
    errors = (errors + data.toString()).slice(-1000)
  })
  const timer = setTimeout(() => child.kill(), 210000)
  child.stdin.on('error', () => {
    /* remote watchdog/EXIT restore still applies */
  })
  try {
    await new Promise<void>((resolve, reject) => {
      child.stdout.on('data', (data) => {
        output += data.toString()
        if (output.split(/\r?\n/).includes('STOPPED')) resolve()
      })
      child.once('error', reject)
      child.once('close', () => {
        if (!output.includes('STOPPED')) reject(new Error('Task service controller did not confirm stop: ' + errors))
      })
    })
    return await operation()
  } finally {
    if (!child.stdin.destroyed) child.stdin.end('restore\n')
    const code = await done
    clearTimeout(timer)
    assert.ok(
      code === 0 && output.split(/\r?\n/).includes('RESTORED'),
      'Task MemOS restoration failed; recover this owned service before concluding',
    )
  }
}
