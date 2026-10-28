import { ApiError } from '../errors.ts'
import { truncateGraphemes } from '../text.ts'
import { paginate, timeRange } from '../query.ts'
import { memoryBucket, selectMemoryTab, toDashboardMemory } from '../adapters/memory-mapper.ts'
import type { WorkspaceReader, NativeMemoryClient, WorkspaceBinding, NativeMemory, DashboardInput } from '../types.ts'

export class MemoryService {
  declare workspaces: Pick<WorkspaceReader, 'all' | 'resolveWorkspace'>
  declare client: NativeMemoryClient
  constructor(workspaces: Pick<WorkspaceReader, 'all' | 'resolveWorkspace'>, client: NativeMemoryClient) {
    this.workspaces = workspaces
    this.client = client
  }
  async listRaw(binding: WorkspaceBinding, type?: string) {
    const response = await this.client.getMemory(binding, memoryBucket(type))
    return selectMemoryTab(response?.data, type, binding)
  }
  async owned(id: string, type?: string) {
    return (await this.ownedMany([id], type))[0]
  }
  async ownedMany(ids: string[], type?: string) {
    if (type !== undefined) memoryBucket(type)
    const types = type === undefined ? ['text_mem', 'pref_mem', 'tool_mem', 'skill_mem'] : [type]
    const pending = new Set(ids),
      found = new Map<string, { row: NativeMemory; binding: WorkspaceBinding }>()
    // No projectId in detail/delete contract: scope is the explicit configured cube allowlist.
    for (const project of this.workspaces.all()) {
      if (!pending.size) break
      const binding = this.workspaces.resolveWorkspace(project.id)
      for (const tab of types) {
        if (!pending.size) break
        const rows = this.client.queryMemory
          ? (await this.client.queryMemory(binding, { memType: tab }, [...pending])).rows
          : await this.listRaw(binding, tab)
        for (const row of rows)
          if (pending.has(row.id)) {
            found.set(row.id, { row, binding })
            pending.delete(row.id)
          }
      }
    }
    if (pending.size) throw new ApiError('记忆不存在或不属于已配置工作区', 40001, 404)
    return ids.map((id) => found.get(id)!)
  }
  async list(query: DashboardInput) {
    const binding = this.workspaces.resolveWorkspace(query.projectId)
    if (query.knowledgeBaseId) throw new ApiError('自部署服务未提供知识库关联查询', 50100, 501)
    const inRange = timeRange(query.startTime, query.endTime)
    if (this.client.queryMemory) {
      memoryBucket(query.memoryDetailType)
      const data = await this.client.queryMemory(binding, query)
      const size = query.pageSize ?? 10
      const limit = Math.max(64, Math.floor(120000 / Math.max(data.rows.length, 1) / 4))
      const result = {
        total: data.total,
        current: query.pageNum ?? 1,
        size,
        memListItemResponseList: data.rows.map((r) => toDashboardMemory(r, { maxContentLength: limit })),
      }
      if (Buffer.byteLength(JSON.stringify(result)) > 195000) throw new ApiError('列表字段过大，请减少 pageSize', 41300, 413)
      return result
    }
    let rows = (await this.listRaw(binding, query.memoryDetailType)).map((row) => toDashboardMemory(row))
    if (query.startTime || query.endTime) rows = rows.filter((r) => inRange(r.createTime))
    if (query.externalUserId) rows = rows.filter((r) => r.externalUserId === query.externalUserId)
    if (query.subjectType) rows = rows.filter((r) => r.subjectType === query.subjectType)
    if (query.memSearchContent) {
      const q = query.memSearchContent.toLowerCase()
      rows = rows.filter((r) => `${r.memoryContent} ${r.skillName || ''}`.toLowerCase().includes(q))
    }
    rows.sort((a, b) => b.createTime.localeCompare(a.createTime) || a.memoryId.localeCompare(b.memoryId))
    const result = paginate(rows, query, 'memListItemResponseList')
    const limit = Math.max(64, Math.floor(120000 / Math.max(result.memListItemResponseList.length, 1) / 4))
    result.memListItemResponseList = result.memListItemResponseList.map((r) => ({
      ...r,
      memoryContent: truncateGraphemes(r.memoryContent, limit),
    }))
    if (Buffer.byteLength(JSON.stringify(result)) > 195000) throw new ApiError('列表字段过大，请减小 pageSize', 41300, 413)
    return result
  }
  async detail(id: string, type?: string) {
    return toDashboardMemory((await this.owned(id, type)).row)
  }
  async delete(ids: string[], type?: string) {
    if (type !== undefined) memoryBucket(type)
    const unique = [...new Set(ids)]
    if (unique.length > 100) throw new ApiError('单次最多删除 100 条记忆')
    const authorized = await this.ownedMany(unique, type)
    const scopes = new Set(authorized.map((x) => JSON.stringify(x.binding.principal)))
    if (scopes.size > 1) throw new ApiError('单次删除不能跨工作区，整批未执行', 40000, 400)
    if (unique.length) await this.client.deleteMemory(unique, authorized[0].binding)
    return { deleted: unique.length }
  }
}
