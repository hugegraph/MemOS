import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync, readdirSync } from 'node:fs'
import { resolve, join } from 'node:path'
import { execFileSync } from 'node:child_process'
import ts from 'typescript'

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory() ? sources(join(dir, entry.name)) : [resolve(dir, entry.name)],
  )
}

test('TS tools: all owned Node sources are TypeScript and included in strict checking', () => {
  const files = [...sources('scripts'), ...sources('deploy/dashboard-bff')]
  assert.deepEqual(
    files.filter((file) => file.endsWith('.mjs')),
    [],
  )
  const config = ts.readConfigFile('tsconfig.tools.json', ts.sys.readFile)
  assert.equal(config.error, undefined)
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, process.cwd())
  assert.deepEqual(parsed.errors, [])
  assert.equal(parsed.options.strict, true)
  assert.equal(parsed.options.erasableSyntaxOnly, true)
  assert.equal(parsed.options.noEmit, true)
  assert.notEqual(parsed.options.noCheck, true)
  const included = new Set(parsed.fileNames.map((file) => resolve(file)))
  for (const file of files.filter((file) => file.endsWith('.ts'))) assert.ok(included.has(file), file)
  const pkg = JSON.parse(readFileSync('package.json', 'utf8')) as { scripts: Record<string, string> }
  assert.ok(pkg.scripts.typecheck.includes('tsc -p tsconfig.tools.json'))
})

test('TS tools: remote audit source links in native TypeScript stdin mode without running its body', () => {
  const input = readFileSync('deploy/dashboard-bff/prepare-browser-audit.ts', 'utf8')
  const source = ts.createSourceFile('audit.ts', input, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  for (const statement of source.statements)
    if (ts.isImportDeclaration(statement) && !statement.importClause?.isTypeOnly)
      assert.ok(ts.isStringLiteral(statement.moduleSpecifier) && statement.moduleSpecifier.text.startsWith('node:'))
  // Node 22.19's --check treats module-typescript stdin as CJS. Exercise the actual
  // launch mode instead; only builtin imports link before this early exit.
  execFileSync(process.execPath, ['--input-type=module-typescript'], {
    input: 'process.exit(0);\n' + input,
    timeout: 15000,
    windowsHide: true,
  })
  assert.ok(readFileSync('scripts/issue-acceptance/real/prepare-audit.ts', 'utf8').includes("'--input-type=module-typescript'"))
})

for (const name of ['verify-workspaces', 'verify-prompts'])
  test(`TS tools: ${name} without write opt-in makes zero calls`, () => {
    let stdout: string
    let code: unknown
    try {
      stdout = execFileSync(process.execPath, [`deploy/dashboard-bff/${name}.ts`], {
        env: { ...process.env, WORKSPACE_VERIFY_MANIFEST: '', PROMPT_VERIFY_MANIFEST: '' },
        encoding: 'utf8',
        timeout: 15000,
        windowsHide: true,
      })
    } catch (error) {
      const failure = error as { status?: number; stdout?: string }
      code = failure.status
      stdout = failure.stdout ?? ''
    }
    assert.equal(code, 1)
    const report = JSON.parse(stdout) as { status: string; calls: number; checks: unknown[] }
    assert.equal(report.status, 'FAIL')
    assert.equal(report.calls, 0)
    assert.deepEqual(report.checks, [])
  })
