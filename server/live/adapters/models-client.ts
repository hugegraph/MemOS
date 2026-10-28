import type { ModelRecord } from '../../../shared/contract.ts'
import type { NativeRequester } from '../types.ts'
import { ApiError } from '../errors.ts'

/** Runtime inventory, not a provider availability probe or a static model list. */
export class ModelsClient {
  declare client: NativeRequester
  declare token: string
  constructor(client: NativeRequester, token: string) {
    this.client = client
    this.token = token
  }

  async list(): Promise<{ records: ModelRecord[] }> {
    if (!this.token) throw new ApiError('未配置原生模型清单扩展令牌', 50100, 501)
    const result = await this.client.request('/product/dashboard-memory/models', undefined, {
      headers: { 'X-Dashboard-Prompt-Token': this.token },
    })
    const data = result.data as { records?: unknown } | undefined
    if (!data || !Array.isArray(data.records) || data.records.length > 64) throw new ApiError('原生模型清单响应无效', 50200, 502)
    const sources = new Set<string>()
    const records = data.records.map((value: unknown): ModelRecord => {
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ApiError('原生模型条目无效', 50200, 502)
      const row = value as Record<string, unknown>
      for (const key of ['role', 'model', 'source', 'spec'] as const) {
        if (
          typeof row[key] !== 'string' ||
          row[key].length > 512 ||
          [...row[key]].some((c) => c.charCodeAt(0) < 32 || c.charCodeAt(0) === 127)
        )
          throw new ApiError('原生模型字段无效', 50200, 502)
      }
      if (!row.model || !row.role || !row.source || !['memory', 'chat'].includes(String(row.group)) || sources.has(String(row.source)))
        throw new ApiError('原生模型标识无效', 50200, 502)
      sources.add(row.source as string)
      // Never forward arbitrary config/credential fields, even on a valid row.
      return {
        role: row.role as string,
        model: row.model as string,
        spec: row.spec as string,
        source: row.source as string,
        group: row.group as ModelRecord['group'],
      }
    })
    return { records }
  }
}
