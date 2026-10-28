/** Test-only in-memory upstream oracle; not native-storage acceptance. No production mapper import. */
import type { NativeMemory } from './types.ts'
export function queryFixture(rows: { cube: string; type: string; row: NativeMemory }[], body: Record<string, unknown>) {
  const tab = String(body.tab)
  const base = ['event_mem', 'profile_mem'].includes(tab) ? 'text_mem' : tab
  const typeOf = (r: NativeMemory) =>
    r.metadata?.dashboard_type ||
    (['text_mem', 'event_mem', 'profile_mem'].includes(String(r.metadata?.type)) ? r.metadata?.type : undefined) ||
    ({ UserMemory: 'profile_mem', Context: 'event_mem', InsightMemory: 'event_mem' } as Record<string, string>)[
      String(r.metadata?.memory_type)
    ]
  let selected = rows
    .filter((r) => r.cube === body.mem_cube_id && r.type === base)
    .map((r) => r.row)
    .filter((r) => (r.metadata?.status ?? 'activated') === 'activated')
  if (tab !== base) selected = selected.filter((r) => typeOf(r) === tab)
  if (Array.isArray(body.ids)) selected = selected.filter((r) => (body.ids as string[]).includes(r.id))
  if (body.search)
    selected = selected.filter((r) =>
      `${r.memory} ${r.metadata?.skill_name ?? ''}`.toLowerCase().includes(String(body.search).toLowerCase()),
    )
  if (body.external_user_id) selected = selected.filter((r) => (r.metadata?.user_id ?? r.metadata?.user_name) === body.external_user_id)
  if (body.subject_type) selected = selected.filter((r) => (r.metadata?.subject_type ?? 'user') === body.subject_type)
  if (body.start_time) selected = selected.filter((r) => Date.parse(String(r.metadata?.created_at)) >= Date.parse(String(body.start_time)))
  if (body.end_time) selected = selected.filter((r) => Date.parse(String(r.metadata?.created_at)) <= Date.parse(String(body.end_time)))
  selected = [...new Map(selected.map((r) => [r.id, r])).values()].sort(
    (a, b) => String(b.metadata?.created_at ?? '').localeCompare(String(a.metadata?.created_at ?? '')) || a.id.localeCompare(b.id),
  )
  const total = selected.length,
    size = Number(body.page_size ?? 10),
    page = Number(body.page ?? 1)
  if (!body.ids) selected = selected.slice((page - 1) * size, page * size)
  return { rows: selected, total, user_id: body.user_id, mem_cube_id: body.mem_cube_id }
}
