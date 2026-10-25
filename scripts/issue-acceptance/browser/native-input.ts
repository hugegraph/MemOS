import assert from 'node:assert/strict'
import type { NativeInputContext, ClickPoint } from './types.ts'

export async function clickNative({ send, evaluate, waitFor, observe = () => {} }: NativeInputContext, expression: string) {
  await send('Page.bringToFront')
  assert.ok(
    await evaluate(
      `(() => {const e=${expression};if(!e||e.disabled)return false;e.scrollIntoView({behavior:'instant',block:'center',inline:'center'});return true})()`,
    ),
    'Clickable control missing or disabled',
  )
  // Bounded external polling works even when background RAF is suspended.
  let target!: ClickPoint | null, previous: ClickPoint | null | undefined
  await waitFor(
    async () => {
      target = (await evaluate(
        `(() => {const e=${expression};if(!e||e.disabled)return null;const r=e.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2,hit=document.elementFromPoint(x,y);return {x,y,hit:!!hit&&(hit===e||e.contains(hit)),targetTag:e.tagName,hitTag:hit?.tagName,role:e.getAttribute('role'),visibility:document.visibilityState,focused:document.hasFocus()}})()`,
      )) as ClickPoint | null
      observe(target)
      const stable =
        target?.hit &&
        target.visibility === 'visible' &&
        previous &&
        Math.abs(target.x - previous.x) < 0.25 &&
        Math.abs(target.y - previous.y) < 0.25
      previous = target
      return stable
    },
    'stable unobscured native click target',
    5000,
  )
  const point = { x: target!.x, y: target!.y }
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...point })
  for (const type of ['mousePressed', 'mouseReleased'])
    await send('Input.dispatchMouseEvent', { type, ...point, button: 'left', buttons: type === 'mousePressed' ? 1 : 0, clickCount: 1 })
}
