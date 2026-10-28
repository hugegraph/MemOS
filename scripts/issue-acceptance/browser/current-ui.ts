import type { Check } from '../../tool-types.ts'

// Frozen 481a291 renders table skeletons and preserves the actual backend error.
// These are observations, not runtime rewrites of arbitrary browser expressions.
export const tableLoading = `!!document.querySelector('tbody [class*="shimmer"]')`
export const tableError = `(() => { const e=document.querySelector('tbody td[colspan]'); return !!e && !!e.innerText.trim() && !e.innerText.includes('暂无数据') && !e.querySelector('[class*="shimmer"]'); })()`
export const tableEmpty = `!!document.querySelector('tbody')?.innerText.includes('暂无数据')`
export function picker(name: string) {
  return `[...document.querySelectorAll('button')].find(e => e.querySelector('span.truncate')?.textContent === ${JSON.stringify(name)})`
}
export function accepted(row: Check) {
  return row.status === 'PASS' || (row.id === 'UI-LIVE' && row.status === 'WAIVED' && row.waiver === 'missing-live-badge')
}

export function outcome(rows: Check[], selected?: Set<string> | null): 'PASS' | 'FAIL' | 'BLOCKED' {
  const judged = rows.filter((row) =>
    selected ? selected.has(row.id ?? '') || row.id === 'UI-ENVIRONMENT' : row.requirement === 'required',
  )
  if (judged.some((row) => row.status === 'FAIL')) return 'FAIL'
  if (
    !judged.length ||
    judged.some((row) => !accepted(row)) ||
    (selected && [...selected].some((id) => !rows.some((row) => row.id === id)))
  )
    return 'BLOCKED'
  return 'PASS'
}
