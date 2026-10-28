import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { approvedRevision, frontendPaths, blobHash, verifyFrontend } from '../frontend-baseline.ts'
import type { TestContext } from 'node:test'

function fixture(t: TestContext) {
  const root = mkdtempSync(join(tmpdir(), 'frontend-baseline-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  mkdirSync(join(root, 'src'))
  const bytes = Buffer.from('export const value = 1\n')
  writeFileSync(join(root, 'src/app.ts'), bytes)
  return { root, baseline: { revision: approvedRevision, paths: frontendPaths, files: { 'src/app.ts': blobHash(bytes) } } }
}
test('frontend gate verifies approved bytes without relying on HEAD/index or git availability', (t) => {
  const { root, baseline } = fixture(t)
  assert.equal(verifyFrontend(root, baseline).files, 1)
})
test('frontend gate rejects content, formatting, missing and additional files', (t) => {
  const { root, baseline } = fixture(t)
  writeFileSync(join(root, 'src/app.ts'), 'export const value = 1\r\n')
  assert.throws(() => verifyFrontend(root, baseline), /differs/)
  rmSync(join(root, 'src/app.ts'))
  assert.throws(() => verifyFrontend(root, baseline), /Missing/)
  writeFileSync(join(root, 'src/app.ts'), 'export const value = 1\n')
  writeFileSync(join(root, 'src/extra.ts'), '')
  assert.throws(() => verifyFrontend(root, baseline), /additional/)
})
test('frontend gate rejects unapproved revision and narrower protection scope', (t) => {
  const { root, baseline } = fixture(t)
  assert.throws(() => verifyFrontend(root, { ...baseline, revision: 'HEAD' }), /Unapproved/)
  assert.throws(() => verifyFrontend(root, { ...baseline, paths: ['src'] }), /paths changed/)
})
