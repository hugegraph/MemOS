import { readFileSync } from 'node:fs'
import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'
import type { IncomingMessage } from 'node:http'
import type { Settings, DashboardInput, OpenApiSpec, Operation } from './types.ts'
import { ApiError } from './errors.ts'
import { MemOSClient } from './adapters/memos-client.ts'
export { ApiError } from './errors.ts'
export { paginate, timeRange } from './query.ts'

export function config(env: NodeJS.ProcessEnv = process.env): Settings {
  const upstream = new URL(env.MEMOS_API_BASE_URL || 'http://127.0.0.1:8000')
  if (!['http:', 'https:'].includes(upstream.protocol) || upstream.username || upstream.password)
    throw new Error('Invalid MEMOS_API_BASE_URL')
  const timeout = Number(env.MEMOS_TIMEOUT_MS || 10000)
  const port = Number(env.DASHBOARD_PORT || 5281)
  if (!Number.isInteger(timeout) || timeout < 1 || timeout > 120000) throw new Error('Invalid MEMOS_TIMEOUT_MS')
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('Invalid DASHBOARD_PORT')
  return {
    upstream: upstream.href.replace(/\/$/, ''),
    timeout,
    port,
    host: env.DASHBOARD_HOST || '127.0.0.1',
    service: env.VITE_SERVICE_BASE || env.MEMOS_SERVICE_URL || 'http://127.0.0.1:8003',
    origins: new Set(
      (env.DASHBOARD_ORIGINS || 'http://localhost:5274,http://127.0.0.1:5274,http://localhost:5275,http://127.0.0.1:5275').split(','),
    ),
    dataDir: env.DASHBOARD_DATA_DIR || '.data/live',
    auditDir: env.MEMORY_CALL_LOG_DIR || '',
    projects: JSON.parse(env.DASHBOARD_WORKSPACES || '[]') as unknown,
    owner: env.MEMOS_CUBE_OWNER || '',
    gatewayEnv: env.DASHBOARD_GATEWAY_ENV || '',
    promptToken: env.DASHBOARD_PROMPT_TOKEN || '',
    memoryToken: env.DASHBOARD_MEMORY_TOKEN || env.DASHBOARD_PROMPT_TOKEN || '',
  }
}

export async function upstreamRequest(settings: Settings, path: string, body?: unknown) {
  // Compatibility entry point; application services share an injected MemOSClient.
  return new MemOSClient(settings).request(path, body)
}

const spec: OpenApiSpec = JSON.parse(readFileSync(new URL('../../openapi.json', import.meta.url), 'utf8'))
const modelsSpec: OpenApiSpec = JSON.parse(readFileSync(new URL('./models.openapi.json', import.meta.url), 'utf8'))
const ajv = new Ajv2020({ strict: false, allErrors: true })
addFormats(ajv)
export const operations = [spec, modelsSpec].flatMap((document) =>
  Object.entries(document.paths).flatMap(([path, methods]) =>
    Object.entries(methods)
      .filter(([method]) => ['get', 'post'].includes(method))
      .map(([method, operation]) => {
        const schema = operation.requestBody?.content?.['application/json']?.schema
        return {
          path,
          method: method.toUpperCase(),
          pattern: new RegExp(`^${path.replace(/\{[^}]+\}/g, '([^/]+)')}$`),
          operation,
          validate: schema ? ajv.compile({ ...schema, components: document.components }) : null,
        }
      }),
  ),
)

export function validateInput(route: Operation, body: DashboardInput) {
  // The shipped UI switches projects with id/status only; OpenAPI also requires name.
  const validated =
    route.path === '/api/dashboard/project/update' && body.status !== undefined && body.name === undefined ? { ...body, name: '' } : body
  if (route.validate && !route.validate(validated)) throw new ApiError(`请求参数不符合接口定义：${ajv.errorsText(route.validate.errors)}`)
  for (const key of ['pageNum', 'pageSize'] as const) {
    if (body[key] !== undefined && (!Number.isInteger(body[key]) || body[key]! < 1)) throw new ApiError(`${key} 必须为正整数`)
  }
  if (body.pageSize! > 1000) throw new ApiError('pageSize 最大为 1000')
}

export async function readBody(req: IncomingMessage): Promise<DashboardInput> {
  if (req.method === 'GET' || req.method === 'HEAD') return {}
  // The existing UI sends revoke POSTs without a body or Content-Type.
  if (!req.headers['transfer-encoding'] && (!req.headers['content-length'] || req.headers['content-length'] === '0')) return {}
  if (!req.headers['content-type']?.toLowerCase().startsWith('application/json'))
    throw new ApiError('Content-Type 必须为 application/json', 40000, 415)
  let bytes = 0
  const chunks: Buffer[] = []
  for await (const chunk of req) {
    bytes += chunk.length
    if (bytes > 1024 * 1024) throw new ApiError('请求体超过 1 MB', 40000, 413)
    chunks.push(chunk)
  }
  try {
    const value: unknown = JSON.parse(Buffer.concat(chunks).toString('utf8'))
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('object required')
    return value as DashboardInput
  } catch {
    throw new ApiError('请求体必须为合法 JSON 对象')
  }
}
