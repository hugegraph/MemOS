import test from 'node:test'
import assert from 'node:assert/strict'
import { clickNative } from '../browser/native-input.mjs'
function context(values) {
  const events = []
  return {
    events,
    send: async (...e) => events.push(e),
    evaluate: async () => {
      const v = values.shift()
      return v && typeof v === 'object' ? { visibility: 'visible', ...v } : v
    },
    waitFor: async (fn, label, limit) => {
      assert.equal(limit, 5000)
      for (let i = 0; i < 5; i++) if (await fn()) return
      throw Error(label)
    },
  }
}
test('native input waits for stable hit and sends exactly one mouse click', async () => {
  const c = context([true, { x: 1, y: 2, hit: true }, { x: 4, y: 5, hit: true }, { x: 4, y: 5, hit: true }])
  await clickNative(c, 'target')
  assert.deepEqual(
    c.events.map(([m, p]) => [m, p?.type]),
    [
      ['Page.bringToFront', undefined],
      ['Input.dispatchMouseEvent', 'mouseMoved'],
      ['Input.dispatchMouseEvent', 'mousePressed'],
      ['Input.dispatchMouseEvent', 'mouseReleased'],
    ],
  )
  assert.ok(c.events.slice(1).every(([, p]) => p.x === 4 && p.y === 5))
})
test('native input never clicks an obscured target', async () => {
  const c = context([true, ...Array.from({ length: 5 }, () => ({ x: 1, y: 2, hit: false }))])
  await assert.rejects(clickNative(c, 'target'))
  assert.equal(c.events.length, 1)
})
test('native input never clicks a missing/disabled target', async () => {
  const c = context([false])
  await assert.rejects(clickNative(c, 'target'))
  assert.equal(c.events.length, 1)
})
test('native input refuses a hidden page even when geometry hits', async () => {
  const c = context([true, ...Array.from({ length: 5 }, () => ({ x: 1, y: 2, hit: true, visibility: 'hidden' }))])
  await assert.rejects(clickNative(c, 'target'))
  assert.equal(c.events.length, 1)
})
