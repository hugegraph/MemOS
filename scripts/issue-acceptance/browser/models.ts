import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import type { ModelRecord } from '../../../shared/contract.ts'
import type { BrowserContext } from './types.ts'
import { tableEmpty } from './current-ui.ts'

export async function modelsCase(ctx: BrowserContext) {
  const { navigate, apiResponsesSince, evaluate, waitFor, click, send } = ctx
  const labels = JSON.parse(await readFile('src/locales/zh.json', 'utf8')) as Record<string, string>
  const start = await navigate('/cn/models')
  const replies = await apiResponsesSince<{ records: ModelRecord[] }>(start, (r) => r.path === '/api/dashboard/models')
  const records = replies.at(-1)!.data.records
  assert.ok(
    records.some((r) => r.group === 'memory'),
    'Real initialized memory models required; empty fake catalog cannot pass',
  )
  if (process.env.ISSUE_MODELS_ORACLE) {
    const oracle = JSON.parse(await readFile(process.env.ISSUE_MODELS_ORACLE, 'utf8')) as { records: ModelRecord[] }
    assert.deepEqual(records, oracle.records, 'BFF must match independently captured native inventory')
  }
  for (const group of ['memory', 'chat'] as const) {
    await click(
      `[...document.querySelectorAll('button[role="tab"]')].find(e=>e.innerText.trim()===${JSON.stringify(labels['models.tab.' + group])})`,
    )
    const expected = records.filter((r) => r.group === group)
    if (!expected.length) {
      await waitFor(() => evaluate(tableEmpty), 'disabled/unconfigured model group empty')
      continue
    }
    await waitFor(
      () =>
        evaluate(
          `document.querySelectorAll('tbody tr').length===${expected.length} && document.querySelectorAll('tbody button[aria-label=${JSON.stringify(labels['models.copyModelName'])}]').length===${expected.length}`,
        ),
      'model rows rendered',
    )
    const actual = await evaluate(
      `[...document.querySelectorAll('tbody tr')].map(r=>[...r.querySelectorAll('td')].slice(0,2).map(c=>c.innerText.trim()))`,
    )
    assert.deepEqual(
      actual,
      expected.map((r) => [r.model, r.role]),
    )
    await send('Browser.grantPermissions', {
      origin: new URL(process.env.ISSUE_WEB_BASE!).origin,
      permissions: ['clipboardReadWrite', 'clipboardSanitizedWrite'],
    })
    await click(`document.querySelector('tbody button[aria-label=${JSON.stringify(labels['models.copyModelName'])}]')`)
    // Return only equality, never expose clipboard contents on a failed click.
    await waitFor(
      () => evaluate(`navigator.clipboard.readText().then(text=>text===${JSON.stringify(expected[0].model)})`),
      'copied model name matches rendered native row',
    )
  }
  return {
    rows: records.length,
    nativeOracle: !!process.env.ISSUE_MODELS_ORACLE,
    memoryAndChatTabs: true,
    exactNamesAndRoles: true,
    copyReadback: true,
    modelCalls: 0,
  }
}
