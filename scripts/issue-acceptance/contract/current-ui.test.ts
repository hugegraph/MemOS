import test from 'node:test'
import assert from 'node:assert/strict'
import { runInNewContext } from 'node:vm'
import { tableLoading, tableError, tableEmpty, picker, accepted, outcome } from '../browser/current-ui.ts'
import { isBootstrapCandidate } from '../browser/bootstrap-policy.ts'

for (const [kind, text, skeleton, expected] of [
  ['loading', '', true, [true, false, false]],
  ['empty', '暂无数据', false, [false, false, true]],
  ['error', '上游服务暂不可用', false, [false, true, false]],
  ['ready', null, false, [false, false, false]],
] as const)
  test(`current UI: ${kind} is identified without obsolete translated messages`, () => {
    const cell = text === null ? null : { innerText: text, querySelector: () => (skeleton ? {} : null) }
    const document = {
      querySelector: (selector: string) =>
        selector.includes('shimmer')
          ? skeleton
            ? {}
            : null
          : selector.includes('td[colspan]')
            ? cell
            : { innerText: text ?? 'actual row' },
    }
    assert.deepEqual(
      [tableLoading, tableError, tableEmpty].map((expression) => runInNewContext(expression, { document })),
      [...expected],
    )
  })
test('current UI: picker uses actual name, not initials concatenated with label', () => {
  const button = { innerText: 'AAlpha', querySelector: () => ({ textContent: 'Alpha' }) }
  assert.equal(runInNewContext(picker('Alpha'), { document: { querySelectorAll: () => [button] } }), button)
  assert.equal(runInNewContext(picker('Other'), { document: { querySelectorAll: () => [button] } }), undefined)
})
test('current UI: waiver cannot turn model errors or other missing features into pass', () => {
  assert.equal(accepted({ id: 'UI-LIVE', status: 'WAIVED', waiver: 'missing-live-badge' }), true)
  for (const id of ['UI-PAGE-models', 'UI-USAGE-TOTALS', 'UI-MEMORY-FILTER'])
    assert.equal(accepted({ id, status: 'WAIVED', waiver: 'missing-live-badge' }), false)
  assert.equal(accepted({ id: 'UI-LIVE', status: 'FAIL' }), false)
  assert.equal(accepted({ id: 'UI-LIVE', status: 'WAIVED' }), false)
})
test('current UI: model404 is not a missing-workspace bootstrap rejection', () => {
  assert.equal(isBootstrapCandidate({ path: '/api/dashboard/models', method: 'GET', started: 0 }), false)
  assert.equal(isBootstrapCandidate({ path: '/api/dashboard/project/memory/list', method: 'POST', started: 0, body: {} }), true)
  assert.equal(
    isBootstrapCandidate({ path: '/api/dashboard/project/memory/list', method: 'POST', started: 0, body: { projectId: 'A' } }),
    false,
  )
})

test('current UI: blocked frontend criteria stay BLOCKED, never PASS or a false execution FAIL', () => {
  const rows = [
    { id: 'models', requirement: 'required', status: 'PASS' },
    { id: 'usage', requirement: 'required', status: 'BLOCKED' },
  ]
  assert.equal(outcome(rows), 'BLOCKED')
  assert.equal(outcome(rows, new Set(['models', 'usage'])), 'BLOCKED')
  assert.equal(outcome(rows, new Set(['models'])), 'PASS')
  assert.equal(outcome(rows, new Set(['not-run'])), 'BLOCKED')
  assert.equal(outcome([...rows, { id: 'UI-ENVIRONMENT', requirement: 'required', status: 'FAIL' }], new Set(['models'])), 'FAIL')
  assert.equal(outcome([{ id: 'UI-LIVE', requirement: 'required', status: 'WAIVED', waiver: 'missing-live-badge' }]), 'PASS')
  assert.equal(outcome([{ id: 'models', requirement: 'required', status: 'WAIVED', waiver: 'missing-live-badge' }]), 'BLOCKED')
})
