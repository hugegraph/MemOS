import { ApiError } from '../errors.ts'
import { truncateGraphemes } from '../text.ts'
import type { Memory } from '../../../shared/contract.ts'
import type { MemoryMetadata, NativeMemory, NativeMemoryGroup, Principal } from '../types.ts'

// Native UI semantic mapping is explicit; Working/LongTerm are not inferred events.
// text_mem remains the complete textual view; event/profile are intentional overlapping subsets.
const tabs = Object.freeze({
  text_mem: 'text_mem',
  pref_mem: 'pref_mem',
  tool_mem: 'tool_mem',
  skill_mem: 'skill_mem',
  event_mem: 'text_mem',
  profile_mem: 'text_mem',
})
export function memoryBucket(tab?: string) {
  if (!Object.hasOwn(tabs, tab!)) throw new ApiError('未知记忆标签')
  return tabs[tab as keyof typeof tabs]
}

export function explicitMemoryType(metadata: MemoryMetadata | null = {}) {
  const supported = ['text_mem', 'event_mem', 'profile_mem']
  const native = supported.includes(metadata?.type as string) ? metadata!.type : undefined
  const extension = metadata?.dashboard_type
  if (extension !== undefined && extension !== null && !supported.includes(extension))
    throw new ApiError('上游 dashboard_type 标记无效', 50200, 502)
  if (native && extension && native !== extension) throw new ApiError('上游记忆分类标记冲突', 50200, 502)
  const origin = ({ UserMemory: 'profile_mem', Context: 'event_mem', InsightMemory: 'event_mem' } as Record<string, string>)[
    String(metadata?.memory_type)
  ]
  const explicit = extension || native
  if (explicit && origin && explicit !== origin) throw new ApiError('原生记忆类型与显式分类冲突', 50200, 502)
  return explicit || origin
}

/** Scope first, then status/ID normalization. No flattening across unrelated cubes. */
export function selectMemoryTab(data: unknown, tab: string | undefined, binding: { principal: Pick<Principal, 'cubeId'> }): NativeMemory[] {
  const groups = (data as Record<string, NativeMemoryGroup[]> | null | undefined)?.[memoryBucket(tab)]
  if (!Array.isArray(groups)) throw new ApiError('上游记忆响应结构不符合预期', 50200, 502)
  if (groups.length && !groups.some((g) => g?.cube_id === binding.principal.cubeId))
    throw new ApiError('上游分组身份与请求 cube 不一致；拒绝将身份异常报告为空数据', 50200, 502)
  const unique = new Map<string, NativeMemory>()
  for (const group of groups) {
    if (group?.cube_id !== binding.principal.cubeId) continue
    if (!Array.isArray(group.memories)) throw new ApiError('上游记忆列表缺少 memories', 50200, 502)
    for (const row of group.memories) {
      if (!row || typeof row.id !== 'string' || !row.id) throw new ApiError('上游记忆缺少有效 ID', 50200, 502)
      if ((row.metadata?.status ?? 'activated') !== 'activated') continue
      const marker = explicitMemoryType(row.metadata)
      if (['event_mem', 'profile_mem'].includes(tab!) && marker !== tab) continue
      unique.set(row.id, row)
    }
  }
  return [...unique.values()]
}

/** Public memory projection is checked against the shared Memory contract. */
export function toDashboardMemory(row: NativeMemory, { maxContentLength = Infinity } = {}): Memory {
  const metadata = row.metadata || {}
  const text = typeof row.memory === 'string' ? row.memory : JSON.stringify(row.memory ?? '')
  const time = Date.parse(metadata.created_at!)
  const result: Memory = {
    memoryId: String(row.id),
    memoryContent: truncateGraphemes(text, maxContentLength) + (row.memory_truncated && text.length <= maxContentLength ? '…' : ''),
    subjectType: ['user', 'agent', 'public'].includes(metadata.subject_type!) ? metadata.subject_type! : 'user',
    externalUserId: String(metadata.user_id ?? metadata.user_name ?? ''),
    createTime: Number.isFinite(time) ? new Date(time).toISOString() : '',
  }
  if (typeof metadata.memory_type === 'string') result.memoryType = metadata.memory_type
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
