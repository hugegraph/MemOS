import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createServer } from 'node:net'
import { setTimeout as delay } from 'node:timers/promises'
import { COLLECTIONS, SCHEMA_VERSION } from '../server/mock/data.ts'

export const types = ['text_mem', 'pref_mem', 'event_mem', 'skill_mem', 'tool_mem', 'profile_mem']
export const ids = (rows, field = 'memoryId') => rows.map((r) => r[field]).sort()
export function fixture(count = 21) {
  const db = structuredClone(COLLECTIONS)
  db.projects = ['A', 'B', 'EMPTY'].map((id) => ({ id, name: id, status: id === 'A' ? 'ENABLED' : 'DISABLED' }))
  db.memories = ['A', 'B'].flatMap((projectId) =>
    types.flatMap((type) =>
      Array.from({ length: count }, (_, i) => ({
        id: `${projectId}-${type}-${i}`,
        memoryId: `${projectId}-${type}-${i}`,
        projectId,
        memoryDetailType: type,
        memoryContent: `中文 marker-${i} ${projectId}`,
        externalUserId: i % 2 ? 'u1' : 'u0',
        subjectType: i % 2 ? 'agent' : 'user',
        createTime: `2026-10-${i < 10 ? '01' : '02'} 12:00:00`,
        metadata: { embedding: Array(1024).fill(0.1), sources: 'x'.repeat(30000) },
        history: 'x'.repeat(30000),
      })),
    ),
  )
  db.logs = ['A', 'B'].flatMap((projectId) =>
    Array.from({ length: 6 }, (_, i) => ({
      id: `${projectId}-log-${i}`,
      projectId,
      traceId: `trace-${projectId}-${i}`,
      createTime: `2026-10-${i < 3 ? '01' : '02'} 12:00:00`,
      eventType: i % 2 ? 'searchMemory' : 'addMessage',
      status: i === 5 ? 'FAILED' : 'SUCCESS',
      userId: `u${i % 2}`,
      userIds: [`u${i % 2}`],
      agentId: `a${i % 2}`,
      agentIds: [`a${i % 2}`],
      duration: i + 1,
      requestTokens: 0,
      responseTokens: 0,
      requestBody: JSON.stringify({ marker: i }),
      responseBody: JSON.stringify({ ok: i !== 5 }),
    })),
  )
  return db
}

export async function harness(t, db = fixture()) {
  const dir = await mkdtemp(join(tmpdir(), 'memos-boundary-'))
  const file = join(dir, 'db.json')
  await writeFile(file, JSON.stringify({ __v: SCHEMA_VERSION, ...db }))
  let child
  async function stop() {
    if (child && child.exitCode === null && child.signalCode === null) {
      const done = once(child, 'exit')
      child.kill()
      await done
    }
  }
  t.after(async () => {
    await stop()
    await rm(dir, { recursive: true, force: true })
  })
  const socket = createServer()
  socket.listen(0, '127.0.0.1')
  await once(socket, 'listening')
  const port = socket.address().port
  await new Promise((resolve) => socket.close(resolve))
  const base = `http://127.0.0.1:${port}`
  async function start() {
    let output = '',
      error
    child = spawn(process.execPath, ['server/mock/server.ts'], {
      cwd: new URL('../', import.meta.url),
      env: { ...process.env, MOCK_PORT: String(port), MOCK_DB: file },
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    child.on('error', (e) => {
      error = e
    })
    child.stdout.on('data', (c) => {
      output += c
    })
    child.stderr.on('data', (c) => {
      output += c
    })
    for (let i = 0; i < 100; i++) {
      if (error || child.exitCode !== null) throw new Error(`Startup failed: ${error || output}`)
      try {
        if ((await fetch(`${base}/api/meta`, { signal: AbortSignal.timeout(300) })).ok) return
      } catch {
        /* not ready */
      }
      await delay(50)
    }
    throw new Error(`Startup timeout: ${output}`)
  }
  await start()
  async function raw(path, body, method = 'POST') {
    return fetch(`${base}/api/dashboard/${path}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: method === 'GET' ? undefined : typeof body === 'string' ? body : JSON.stringify(body ?? {}),
      signal: AbortSignal.timeout(3000),
    })
  }
  async function call(path, body, method = 'POST') {
    const response = await raw(path, body, method)
    assert.ok(response.ok)
    const result = await response.json()
    assert.equal(result.code, 0, JSON.stringify(result))
    return result.data
  }
  return {
    base,
    call,
    raw,
    restart: async () => {
      await stop()
      await start()
    },
  }
}
