import { ApiError } from '../errors.ts'
import type { NativeRequester, WorkspaceBinding, DashboardInput, PromptData } from '../types.ts'

/** Private extension transport; provider credentials never leave MemOS. */
export class PromptClient {
  declare client: NativeRequester
  declare token: string
  constructor(client: NativeRequester, token: string) {
    this.client = client
    this.token = token
  }
  async execute(action: string, binding: Pick<WorkspaceBinding, 'principal'>, payload: DashboardInput): Promise<PromptData> {
    if (!this.token) throw new ApiError('上游未启用工作区 Prompt 扩展，未保存或生效', 50100, 501)
    if (!['detail', 'save', 'generate', 'reset-default', 'debug/extract'].includes(action)) throw new ApiError('未知 Prompt 操作')
    const result = await this.client.request(
      `/product/dashboard-prompt/${action}`,
      {
        ...payload,
        user_id: binding.principal.userId,
        mem_cube_id: binding.principal.cubeId,
      },
      { headers: { 'X-Dashboard-Prompt-Token': this.token }, timeout: 120000 },
    )
    const data = result.data as PromptData | undefined
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new ApiError('上游 Prompt 响应无效', 50200, 502)
    if (['detail', 'generate', 'reset-default'].includes(action) && (typeof data.prompt !== 'string' || data.prompt.length > 12000))
      throw new ApiError('上游 Prompt 内容无效', 50200, 502)
    if (action === 'save' && data.ok !== true) throw new ApiError('上游未确认 Prompt 保存', 50200, 502)
    if (
      action === 'debug/extract' &&
      (!Array.isArray(data.results) ||
        data.results.length > 30 ||
        data.extractionTypes?.length !== 1 ||
        data.extractionTypes[0] !== 'text_mem' ||
        data.results.some((r) => r?.type !== 'text_mem' || typeof r.content !== 'string' || r.content.length > 8000))
    )
      throw new ApiError('上游抽取结果无效', 50200, 502)
    return data
  }
}
