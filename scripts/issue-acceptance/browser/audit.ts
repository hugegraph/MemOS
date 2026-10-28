// Match native JSONL oracle metadata/body hashes to the rendered log detail dialog.
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import type { BrowserContext } from './types.ts'
import type { LogPage } from '../../tool-types.ts'
import type { LogDetail } from '../../../shared/contract.ts'

export async function auditCase(
  ctx: Pick<BrowserContext, 'navigate' | 'waitFor' | 'evaluate' | 'click' | 'apiResponsesSince'>,
  file: string,
) {
  const { navigate, waitFor, evaluate, click, apiResponsesSince } = ctx
  const oracle = JSON.parse(await readFile(file, 'utf8'))
  assert.equal(oracle.status, 'PASS')
  assert.equal(oracle.rawRecordFound, 1)
  assert.equal(oracle.fieldsMatch, true)
  const canonical = (v: unknown): unknown =>
    Array.isArray(v)
      ? v.map(canonical)
      : v && typeof v === 'object'
        ? Object.fromEntries(
            Object.keys(v)
              .sort()
              .map((k) => [k, canonical((v as Record<string, unknown>)[k])]),
          )
        : v
  const hash = (v: unknown) =>
    createHash('sha256')
      .update(JSON.stringify(canonical(v)))
      .digest('hex')
  const start = await navigate('/cn/requests')
  const replies = await apiResponsesSince<LogPage>(start, (r) => r.path.endsWith('/log/list'))
  const row = replies.at(-1)!.data.logs.find((r) => r.id === oracle.logId)
  assert.ok(row, 'Fresh MCP record must appear on the current first page')
  for (const key of ['createTime', 'eventType', 'duration']) assert.equal(row[key], oracle[key])
  assert.equal(row.status, oracle.eventStatus)
  const selector = `[...document.querySelectorAll('tbody tr')].find(r=>r.textContent.includes(${JSON.stringify(oracle.createTime)}) && r.textContent.includes(${JSON.stringify(oracle.eventType)}) && r.textContent.includes(${JSON.stringify(oracle.duration + 'ms')}))`
  await waitFor(() => evaluate(`!!(${selector})`), 'fresh correlated row visible')
  const selected = Date.now()
  await click(`(${selector})?.querySelector('td')`)
  await waitFor(
    () =>
      evaluate(
        `!!document.querySelector('[role="dialog"] pre') && !document.querySelector('[role="dialog"]').innerText.includes('加载中…')`,
      ),
    'log detail ready',
  )
  const details = await apiResponsesSince<LogDetail>(selected, (r) => r.path.endsWith('/log/detail'))
  assert.ok(details.length > 0 && details.every((r) => r.data.id === oracle.logId))
  const request = JSON.parse((await evaluate(`document.querySelector('[role="dialog"] pre').textContent`)) as string)
  assert.equal(hash(request), oracle.requestHash)
  assert.equal(request.current, oracle.marker)
  await click(`[...document.querySelectorAll('[role="dialog"] button')].find(e=>e.innerText.trim()==='响应详情')`)
  await waitFor(async () => {
    try {
      return (
        hash(JSON.parse((await evaluate(`document.querySelector('[role="dialog"] pre').textContent`)) as string)) === oracle.responseHash
      )
    } catch {
      return false
    }
  }, 'response tab matches native record hash')
  assert.ok(await evaluate(`document.querySelector('[role="dialog"]').textContent.includes(${JSON.stringify(oracle.createTime)})`))
  await click(`document.querySelector('[role="dialog"] button[aria-label="Close"]')`)
  return {
    rawRecordFound: 1,
    sameLogId: true,
    metadataMatched: true,
    requestBodyHashMatched: true,
    responseBodyHashMatched: true,
    memoryWrites: 0,
    modelCalls: 0,
  }
}
