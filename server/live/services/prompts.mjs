import { ApiError } from '../errors.mjs'

/** Workspace binding is authoritative; request-supplied upstream identities are ignored. */
export class PromptService {
  constructor(workspaces, client) {
    this.workspaces = workspaces
    this.client = client
  }
  async execute(action, body) {
    const binding = this.workspaces.resolveWorkspace(body.projectId)
    let payload = {}
    if (action === 'save') {
      if (typeof body.prompt !== 'string' || body.prompt.length > 12000) throw new ApiError('Prompt 最多12000字符')
      payload = { prompt: body.prompt }
    } else if (action === 'generate') {
      if (typeof body.description !== 'string' || !body.description.trim() || body.description.length > 4000)
        throw new ApiError('生成要求须为1至4000字符')
      payload = { description: body.description }
    } else if (action === 'debug/extract') {
      const types = body.extractionTypes ?? ['text_mem']
      if (!Array.isArray(types) || types.length !== 1 || types[0] !== 'text_mem')
        throw new ApiError('当前仅支持文本聊天抽取调试', 40000, 400)
      const messages = body.messages
      if (
        !Array.isArray(messages) ||
        messages.length < 1 ||
        messages.length > 30 ||
        messages.some(
          (m) => !m || !['user', 'assistant', 'system'].includes(m.role) || typeof m.content !== 'string' || !m.content.trim(),
        ) ||
        messages.reduce((n, m) => n + m.content.length, 0) > 24000
      )
        throw new ApiError('调试需要1至30条纯文本消息，总长度不超过24000字符')
      payload = { messages: messages.map(({ role, content }) => ({ role, content })), extractionTypes: types }
    }
    const data = await this.client.execute(action, binding, payload)
    if (action === 'detail') return { projectId: body.projectId, prompt: data.prompt, updatedAt: data.updatedAt ?? '' }
    if (action === 'save') return { ok: true }
    if (action === 'debug/extract') return { results: data.results, extractionTypes: data.extractionTypes }
    return { prompt: data.prompt }
  }
}

export function promptRoutes(service) {
  return Object.fromEntries(
    ['detail', 'save', 'generate', 'reset-default', 'debug/extract'].map((action) => [
      `POST /api/dashboard/prompt-config/${action}`,
      ({ body }) => service.execute(action, body),
    ]),
  )
}
