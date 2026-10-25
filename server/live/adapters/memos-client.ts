import { ApiError } from '../errors.ts'
import type { Settings, NativeEnvelope, RequestOptions, Transport, WorkspaceBinding } from '../types.ts'

/** Adapter for deployment/src/memos/api/product_models.py, not a second Dashboard API. */
export class MemOSClient {
  declare base: string
  declare timeout: number
  declare managementToken: string | undefined
  declare transport: Transport
  declare maxResponseBytes: number
  constructor(
    settings: Pick<Settings, 'upstream' | 'timeout'> & Partial<Pick<Settings, 'memoryToken'>>,
    { fetch: transport = globalThis.fetch, maxResponseBytes = 32 * 1024 * 1024 }: { fetch?: Transport; maxResponseBytes?: number } = {},
  ) {
    const url = new URL(settings.upstream)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash)
      throw new Error('Invalid MemOS upstream URL')
    if (!Number.isSafeInteger(maxResponseBytes) || maxResponseBytes < 1) throw new Error('Invalid upstream response limit')
    this.base = url.href.replace(/\/$/, '')
    this.timeout = settings.timeout
    this.managementToken = settings.memoryToken
    this.transport = transport
    this.maxResponseBytes = maxResponseBytes
  }

  async request(path: string, body?: unknown, { headers = {}, timeout = this.timeout }: RequestOptions = {}): Promise<NativeEnvelope> {
    if (!/^\/[a-zA-Z0-9_/-]+$/.test(path) || path.startsWith('//')) throw new ApiError('非法上游接口路径')
    try {
      const response = await this.transport(`${this.base}${path}`, {
        method: body === undefined ? 'GET' : 'POST',
        headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(timeout),
        redirect: 'error',
      })
      if (!response.ok) {
        await response.body?.cancel()
        throw new ApiError(`上游请求失败（HTTP ${response.status}）`, 50200, 502)
      }
      const reader = response.body?.getReader()
      if (!reader) throw new ApiError('上游响应为空', 50200, 502)
      const chunks = []
      let bytes = 0
      try {
        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          bytes += value.byteLength
          if (bytes > this.maxResponseBytes) throw new ApiError('上游响应超过安全大小限制，请缩小查询范围', 50200, 502)
          chunks.push(Buffer.from(value))
        }
      } finally {
        await reader.cancel().catch(() => {})
      }
      let result: NativeEnvelope
      try {
        result = JSON.parse(Buffer.concat(chunks).toString('utf8'))
      } catch {
        throw new ApiError('上游返回非 JSON 响应', 50200, 502)
      }
      if (!result || typeof result !== 'object' || Array.isArray(result)) throw new ApiError('上游响应结构无效', 50200, 502)
      if (result.code !== undefined && ![0, 200].includes(result.code)) throw new ApiError('上游返回业务错误', 50200, 502)
      if ((result.data as { status?: unknown } | null | undefined)?.status === 'failure') throw new ApiError('上游操作失败', 50200, 502)
      return result
    } catch (error) {
      if (error instanceof ApiError) throw error
      if (['TimeoutError', 'AbortError'].includes((error as Error | undefined)?.name as string))
        throw new ApiError('上游请求超时', 50400, 504)
      throw new ApiError('无法连接记忆服务，请检查服务地址与运行状态', 50200, 502)
    }
  }

  health() {
    return this.request('/health')
  }
  getMemory(binding: Pick<WorkspaceBinding, 'principal'>, bucket: string) {
    return this.request('/product/get_memory', {
      mem_cube_id: binding.principal.cubeId,
      user_id: binding.principal.userId,
      include_preference: bucket === 'pref_mem',
      include_tool_memory: bucket === 'tool_mem',
      include_skill_memory: bucket === 'skill_mem',
      page: null,
      page_size: null,
    })
  }
  async deleteMemory(ids: string[], binding: Pick<WorkspaceBinding, 'principal'>) {
    if (!this.managementToken) throw new ApiError('未配置受控软删除扩展，已拒绝物理删除回退', 50100, 501)
    const result = await this.request(
      '/product/dashboard-memory/soft-delete',
      {
        memory_ids: ids,
        user_id: binding.principal.userId,
        mem_cube_id: binding.principal.cubeId,
      },
      { headers: { 'X-Dashboard-Prompt-Token': this.managementToken } },
    )
    if (
      (result.data as { soft_deleted?: unknown } | null | undefined)?.soft_deleted !== true ||
      (result.data as { deleted?: unknown } | null | undefined)?.deleted !== ids.length
    )
      throw new ApiError('上游未确认完整软删除，未报告成功', 50200, 502)
    return result
  }
  createCube(body: { cube_name: string; cube_id: string; owner_id: string }) {
    return this.request('/product/create_cube', body)
  }
}
