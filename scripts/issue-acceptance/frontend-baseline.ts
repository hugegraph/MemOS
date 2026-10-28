import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { lstatSync, existsSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
export interface FrontendBaseline {
  revision: string
  paths: string[]
  files: Record<string, string>
}

// Explicitly authorized upstream update; never infer approval from dirty HEAD or an env override.
export const approvedRevision = '481a291e24928fc5eab5fb051b5c653fafa9cbe4'
export const frontendPaths = [
  'src',
  'public',
  'index.html',
  'vite.config.ts',
  'tsconfig.app.json',
  'tsconfig.json',
  'tsconfig.node.json',
  'shared/contract.ts',
]
export const baselineFile = new URL('./frontend-baseline.json', import.meta.url)
export const blobHash = (bytes: Uint8Array) => createHash('sha1').update(`blob ${bytes.length}\0`).update(bytes).digest('hex')

export function verifyFrontend(root: string, baseline: FrontendBaseline) {
  assert.equal(baseline.revision, approvedRevision, 'Unapproved frontend baseline')
  assert.deepEqual(baseline.paths, frontendPaths, 'Protected frontend paths changed')
  const walk = (p: string): string[] => {
    const absolute = join(root, p)
    if (!existsSync(absolute)) return []
    const stat = lstatSync(absolute)
    assert.ok(!stat.isSymbolicLink(), `Unexpected frontend symlink: ${p}`)
    return stat.isDirectory() ? readdirSync(absolute).flatMap((n) => walk(`${p}/${n}`)) : [p]
  }
  const actual = frontendPaths.flatMap(walk).sort()
  assert.deepEqual(actual, Object.keys(baseline.files).sort(), 'Missing or additional frontend files')
  for (const p of actual)
    assert.equal(blobHash(readFileSync(join(root, p))), baseline.files[p], `Frontend differs from approved upstream: ${p}`)
  return { baseline: baseline.revision, paths: frontendPaths, files: actual.length }
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  assert.equal(process.argv[2], '--record-approved-upstream', 'Only explicit upstream recording is supported')
  const root = fileURLToPath(new URL('../../', import.meta.url))
  const entries = execFileSync('git', ['ls-tree', '-r', '-z', approvedRevision, '--', ...frontendPaths], { cwd: root, encoding: 'utf8' })
    .split('\0')
    .filter(Boolean)
    .map((line) => {
      const [metadata, path] = line.split('\t')
      const [mode, type, sha] = metadata.split(' ')
      assert.equal(mode, '100644')
      assert.equal(type, 'blob')
      return [path, sha]
    })
  assert.ok(entries.length > 0)
  // Record git objects, NOT whatever happens to be in the worktree. Runtime verification
  // uses this committed manifest and therefore also works in shallow CI clones.
  writeFileSync(
    baselineFile,
    JSON.stringify({ revision: approvedRevision, paths: frontendPaths, files: Object.fromEntries(entries) }, null, 2) + '\n',
  )
  console.log(`Recorded ${entries.length} upstream frontend blob hashes`)
}
