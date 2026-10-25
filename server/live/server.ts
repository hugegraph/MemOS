import http from 'node:http'
import { pathToFileURL } from 'node:url'
import { ApiError, config, operations, readBody, validateInput } from './core.ts'
import { MemOSClient } from './adapters/memos-client.ts'
import { buildRoutes } from './app.ts'
import type { Dependencies } from './app.ts'
import type { AddressInfo } from 'node:net'
import type { Settings, Routes, NativeClient } from './types.ts'
import type { Meta } from '../../shared/contract.ts'

const tools = [
  { name: 'add_message', title: '写入记忆' },
  { name: 'search_memory', title: '检索记忆' },
  { name: 'get_user_profile', title: '读取画像' },
  { name: 'delete_memory', title: '删除记忆' },
  { name: 'add_feedback', title: '提交反馈' },
]

export function createApp(
  settings: Settings = config(),
  handlers: Routes = {},
  client: Pick<NativeClient, 'health'> = new MemOSClient(settings),
) {
  const routes: Routes = {
    'GET /api/meta': async (): Promise<Meta> => {
      let health
      try {
        health = await client.health()
      } catch (error) {
        health = { status: 'unavailable', message: (error as Error).message, code: (error as ApiError).code }
      }
      return { mode: 'live', service: settings.service, health, keyPrefix: 'sk-', tools }
    },
    ...handlers,
  }
  const server = http.createServer(async (req, res) => {
    const origin = req.headers.origin
    const send = (status: number, code: number, data: unknown, message: string) => {
      res.writeHead(status, {
        'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
      })
      res.end(JSON.stringify({ code, data, message }))
    }
    try {
      if (origin) {
        if (!settings.origins.has(origin)) throw new ApiError('不允许此来源访问管理接口', 40300, 403)
        res.setHeader('Access-Control-Allow-Origin', origin)
        res.setHeader('Vary', 'Origin')
      }
      if (req.method === 'OPTIONS') {
        res.writeHead(204, { 'Access-Control-Allow-Methods': 'GET,POST,OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type' })
        return res.end()
      }
      const url = new URL(req.url!, 'http://localhost')
      const route = operations.find((r) => r.method === req.method && r.pattern.test(url.pathname))
      if (!route) throw new ApiError('接口不存在或请求方法不正确', 40001, 404)
      const body = await readBody(req)
      validateInput(route, body)
      const handler = routes[`${route.method} ${route.path}`]
      if (!handler) throw new ApiError('此功能尚未实现或上游不支持', 50100, 501)
      const params = route.pattern.exec(url.pathname)!.slice(1).map(decodeURIComponent)
      send(200, 0, await handler({ body, query: url.searchParams, params }), 'success')
    } catch (error) {
      if (!(error instanceof ApiError)) console.error('Dashboard request failed:', error)
      send(
        error instanceof ApiError ? error.status : 500,
        error instanceof ApiError ? error.code : 50000,
        null,
        error instanceof ApiError ? error.message : '内部错误',
      )
    }
  })
  server.requestTimeout = 15000
  server.headersTimeout = 10000
  return server
}

export function buildApp(settings = config(), dependencies: Dependencies = {}) {
  const client = dependencies.client ?? new MemOSClient(settings)
  return createApp(settings, buildRoutes(settings, { ...dependencies, client }), client)
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const settings = config()
  const server = buildApp(settings)
  server.on('error', (error) => {
    console.error(error.message)
    process.exitCode = 1
  })
  server.listen(settings.port, settings.host, () =>
    console.log(`Dashboard live backend: http://${settings.host}:${(server.address() as AddressInfo).port}`),
  )
}
