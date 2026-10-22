import { ApiError } from '../errors.mjs'
import { paginate, timeRange } from '../query.mjs'
import { memoryBucket, selectMemoryTab, toDashboardMemory } from '../adapters/memory-mapper.mjs'

export class MemoryService {
  constructor(workspaces, client) {
    this.workspaces = workspaces
    this.client = client
  }
  async listRaw(binding, type) {
    const response = await this.client.getMemory(binding, memoryBucket(type))
    return selectMemoryTab(response?.data, type, binding)
  }
  async owned(id, type) {
    return (await this.ownedMany([id], type))[0]
  }
  async ownedMany(ids, type) {
    memoryBucket(type)
    const pending = new Set(ids),
      found = new Map()
    // No projectId in detail/delete contract: scope is the explicit configured cube allowlist.
    for (const project of this.workspaces.all()) {
      if (!pending.size) break
      const binding = this.workspaces.resolveWorkspace(project.id)
      for (const row of await this.listRaw(binding, type))
        if (pending.has(row.id)) {
          found.set(row.id, { row, binding })
          pending.delete(row.id)
        }
    }
    if (pending.size) throw new ApiError('记忆不存在或不属于已配置工作区', 40001, 404)
    return ids.map((id) => found.get(id))
  }
  async list(query) {
    const binding = this.workspaces.resolveWorkspace(query.projectId)
    if (query.knowledgeBaseId) throw new ApiError('自部署服务未提供知识库关联查询', 50100, 501)
    const inRange = timeRange(query.startTime, query.endTime)
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
      memoryContent: r.memoryContent.length > limit ? `${r.memoryContent.slice(0, limit)}…` : r.memoryContent,
    }))
    if (Buffer.byteLength(JSON.stringify(result)) > 195000) throw new ApiError('列表字段过大，请减小 pageSize', 41300, 413)
    return result
  }
  async detail(id, type) {
    return toDashboardMemory((await this.owned(id, type)).row)
  }
  async delete(ids, type) {
    memoryBucket(type)
    const unique = [...new Set(ids)]
    if (unique.length > 100) throw new ApiError('单次最多删除 100 条记忆')
    const authorized = await this.ownedMany(unique, type)
    const scopes = new Set(authorized.map((x) => JSON.stringify(x.binding.principal)))
    if (scopes.size > 1) throw new ApiError('单次删除不能跨工作区，整批未执行', 40000, 400)
    if (unique.length) await this.client.deleteMemory(unique, authorized[0].binding)
    return { deleted: unique.length }
  }
}
