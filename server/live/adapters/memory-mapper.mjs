import { ApiError } from '../errors.mjs'

// Product mapping approved 2026-10-19: explicit type/dashboard_type, never lifecycle inference.
// text_mem remains the complete textual view; event/profile are intentional overlapping subsets.
const tabs = Object.freeze({
  text_mem: 'text_mem',
  pref_mem: 'pref_mem',
  tool_mem: 'tool_mem',
  skill_mem: 'skill_mem',
  event_mem: 'text_mem',
  profile_mem: 'text_mem',
})
export function memoryBucket(tab) {
  if (!Object.hasOwn(tabs, tab)) throw new ApiError('未知记忆标签')
  return tabs[tab]
}

export function explicitMemoryType(metadata = {}) {
  const supported = ['text_mem', 'event_mem', 'profile_mem']
  const native = supported.includes(metadata?.type) ? metadata.type : undefined
  const extension = metadata?.dashboard_type
  if (extension !== undefined && extension !== null && !supported.includes(extension))
    throw new ApiError('上游 dashboard_type 标记无效', 50200, 502)
  if (native && extension && native !== extension) throw new ApiError('上游记忆分类标记冲突', 50200, 502)
  return extension || native
}

/** Scope first, then status/ID normalization. No flattening across unrelated cubes. */
export function selectMemoryTab(data, tab, binding) {
  const groups = data?.[memoryBucket(tab)]
  if (!Array.isArray(groups)) throw new ApiError('上游记忆响应结构不符合预期', 50200, 502)
  const unique = new Map()
  for (const group of groups) {
    if (group?.cube_id !== binding.principal.cubeId) continue
    if (!Array.isArray(group.memories)) throw new ApiError('上游记忆列表缺少 memories', 50200, 502)
    for (const row of group.memories) {
      if (!row || typeof row.id !== 'string' || !row.id) throw new ApiError('上游记忆缺少有效 ID', 50200, 502)
      if ((row.metadata?.status ?? 'activated') !== 'activated') continue
      const marker = explicitMemoryType(row.metadata)
      if (['event_mem', 'profile_mem'].includes(tab) && marker !== tab) continue
      unique.set(row.id, row)
    }
  }
  return [...unique.values()]
}

/** @returns {import('../../../src/lib/api').Memory} */
export function toDashboardMemory(row, { maxContentLength = Infinity } = {}) {
  const metadata = row.metadata || {}
  const text = typeof row.memory === 'string' ? row.memory : JSON.stringify(row.memory ?? '')
  const time = Date.parse(metadata.created_at)
  const result = {
    memoryId: String(row.id),
    memoryContent: text.length > maxContentLength ? `${text.slice(0, maxContentLength)}…` : text,
    subjectType: ['user', 'agent', 'public'].includes(metadata.subject_type) ? metadata.subject_type : 'user',
    externalUserId: String(metadata.user_id ?? metadata.user_name ?? ''),
    createTime: Number.isFinite(time) ? new Date(time).toISOString() : '',
  }
  if (metadata.skill_name) result.skillName = String(metadata.skill_name)
  if (metadata.skill_url) result.skillUrl = String(metadata.skill_url)
  if (explicitMemoryType(metadata) === 'profile_mem') {
    if (typeof metadata.profile_template_id === 'string' && metadata.profile_template_id)
      result.profileTemplateId = metadata.profile_template_id
    if (typeof metadata.profile_instance_id === 'string' && metadata.profile_instance_id)
      result.profileInstanceId = metadata.profile_instance_id
  }
  return result
}
