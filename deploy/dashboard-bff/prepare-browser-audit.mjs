// Inside BFF, after probe-gateway.py. Export hashes/metadata only, never raw log bodies/tokens.
import assert from 'node:assert/strict'
import { readdir, readFile, stat } from 'node:fs/promises'
import { join } from 'node:path'
import { createHash } from 'node:crypto'
const marker = Number(process.env.AUDIT_VERIFY_MARKER)
const user = process.env.AUDIT_PROBE_USER
const cube = process.env.AUDIT_PROBE_CUBE
assert.ok(user && cube, 'Select the protected audit identity explicitly')
assert.ok(Number.isInteger(marker) && marker >= 100000 && marker < 1000000)
const canonical = (v) =>
  Array.isArray(v)
    ? v.map(canonical)
    : v && typeof v === 'object'
      ? Object.fromEntries(
          Object.keys(v)
            .sort()
            .map((k) => [k, canonical(v[k])]),
        )
      : v
const normalize = (v) => {
  if (typeof v === 'string') {
    try {
      return JSON.parse(v)
    } catch {
      return v
    }
  }
  return v ?? null
}
const hash = (v) =>
  createHash('sha256')
    .update(JSON.stringify(canonical(v)))
    .digest('hex')
const matches = []
for (const name of await readdir(process.env.MEMORY_CALL_LOG_DIR)) {
  if (!/^calls-.*\.jsonl$/.test(name)) continue
  const file = join(process.env.MEMORY_CALL_LOG_DIR, name)
  assert.ok((await stat(file)).size < 32 * 1024 * 1024)
  for (const line of (await readFile(file, 'utf8')).split('\n').slice(0, -1)) {
    let row
    try {
      row = JSON.parse(line)
    } catch {
      continue
    }
    if (row.tenant === user && row.cube_id === cube && row.tool === 'get_user_profile' && normalize(row.input)?.current === marker)
      matches.push(row)
  }
}
assert.equal(matches.length, 1, 'Fresh marker must identify exactly one native JSONL record')
const raw = matches[0],
  createTime = new Date(raw.ts).toISOString()
async function call(path, body) {
  const response = await fetch('http://127.0.0.1:5281/api/dashboard/' + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(5000),
  })
  assert.equal(response.status, 200)
  const reply = await response.json()
  assert.equal(reply.code, 0)
  return reply.data
}
const list = await call('log/list', { projectId: cube, startTime: createTime, endTime: createTime, pageSize: 100 })
let detail
for (const row of list.logs) {
  const value = await call('log/detail', { logId: row.id })
  if (hash(normalize(value.requestBody)) === hash(normalize(raw.input))) {
    assert.equal(detail, undefined)
    detail = value
  }
}
assert.ok(detail)
assert.equal(detail.createTime, createTime)
assert.equal(detail.eventType, 'getMemory')
assert.equal(detail.duration, Number(raw.ms ?? 0))
assert.equal(detail.status, raw.ok === true ? 'SUCCESS' : 'FAILED')
assert.equal(hash(normalize(detail.responseBody)), hash(normalize(raw.output)))
console.log(
  JSON.stringify({
    status: 'PASS',
    projectId: cube,
    marker,
    logId: detail.id,
    createTime,
    eventType: detail.eventType,
    duration: detail.duration,
    eventStatus: detail.status,
    requestHash: hash(normalize(raw.input)),
    responseHash: hash(normalize(raw.output)),
    rawRecordFound: 1,
    fieldsMatch: true,
  }),
)
