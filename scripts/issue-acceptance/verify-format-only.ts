// Legacy entry point: current policy requires zero frontend changes first.
// AST comparison below is supplementary, never a formatting exemption.
import './verify-frontend-unchanged.ts'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { readFile, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import ts from 'typescript'

const require = createRequire(import.meta.url)
const postcss = createRequire(require.resolve('vite/package.json'))('postcss')
const files = execFileSync('git', ['ls-files', 'src/'], { encoding: 'utf8' }).trim().split('\n').filter(Boolean)
function shape(node: ts.Node): unknown {
  if (ts.isParenthesizedExpression(node) || ts.isParenthesizedTypeNode(node))
    return shape((node as ts.ParenthesizedExpression).expression || (node as ts.ParenthesizedTypeNode).type)
  const children: unknown[] = []
  ts.forEachChild(node, (child) => {
    children.push(shape(child))
  })
  return {
    kind: node.kind,
    text: ts.isSourceFile(node) ? undefined : (node as ts.Node & { text?: string }).text,
    escapedText: (node as ts.Node & { escapedText?: string }).escapedText,
    isTypeOnly: (node as ts.Node & { isTypeOnly?: boolean }).isTypeOnly,
    children,
  }
}
function script(source: string, file: string) {
  const code = file.endsWith('.d.ts')
    ? source
    : ts.transpileModule(source, {
        fileName: file,
        compilerOptions: { target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX, removeComments: true },
      }).outputText
  return shape(ts.createSourceFile(file.endsWith('.d.ts') ? file : 'output.js', code, ts.ScriptTarget.Latest, true))
}
function css(source: string) {
  return JSON.parse(
    JSON.stringify(postcss.parse(source.replace(/\r\n/g, '\n')), (key, value) =>
      ['raws', 'source', 'inputs'].includes(key) ? undefined : value,
    ),
  )
}
const checked = []
// Earlier frontend exceptions were withdrawn by the user.
const functionalFiles = new Set()
const authorizedFunctionalChanges = []
for (const file of files) {
  const before = execFileSync('git', ['show', `HEAD:${file}`], { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 })
  const after = await readFile(file, 'utf8')
  if (before === after) continue
  if (functionalFiles.has(file)) {
    authorizedFunctionalChanges.push(file)
    continue
  }
  if (/\.[cm]?[jt]sx?$/.test(file)) assert.deepEqual(script(after, file), script(before, file), `${file}: executable/type AST changed`)
  else if (file.endsWith('.css')) assert.deepEqual(css(after), css(before), `${file}: CSS rules/declarations changed`)
  else assert.equal(after, before, `${file}: unexpected frontend file change`)
  checked.push(file)
}
await writeFile(
  '.data/issue-acceptance/batch5-format-semantics.json',
  JSON.stringify(
    {
      status: 'PASS',
      checked,
      authorizedFunctionalChanges,
      baseline: 'HEAD',
      scope: 'Zero frontend diff required first; static semantic comparison is supplementary; no authorized exceptions',
    },
    null,
    2,
  ),
)
console.log(`PASS: ${checked.length} modified frontend files preserve parsed semantics`)
