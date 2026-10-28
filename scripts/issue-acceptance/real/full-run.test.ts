import test from 'node:test'
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import type { ToolError } from '../../tool-types.ts'
for (const [name, args] of [
  ['without mutation consent', []],
  [
    'without explicit deployment profile',
    ['--allow-write', '--allow-service-stop', '--manifest', 'must-not-read.json', '--audit', 'must-not-read.json'],
  ],
] as const)
  test(`full real gate blocks ${name} before file/network work`, async () => {
    let error: ToolError | undefined
    try {
      await promisify(execFile)(process.execPath, ['scripts/issue-acceptance/real/full-run.ts', ...args])
    } catch (e) {
      error = e as ToolError
    }
    assert.equal(error?.code, 2)
    const report = JSON.parse(error!.stdout!)
    assert.equal(report.status, 'BLOCKED')
    assert.deepEqual(report.phases, [])
    assert.equal(report.output, undefined)
  })
