import { randomUUID } from 'node:crypto'
import { join } from 'node:path'
import { ApiError, paginate } from './core.ts'
import { KeyService } from './services/keys.ts'
import { GatewayConfigAdapter } from './adapters/gateway-config.ts'
import { TemplateRepository } from './repositories/templates.ts'
import type { Settings, WorkspaceReader, StoredTemplate, Routes } from './types.ts'
import type { Dependencies } from './app.ts'

export function managementRoutes(settings: Settings, projects: WorkspaceReader, dependencies: Dependencies = {}): Routes {
  const keys =
    dependencies.keys ??
    new KeyService(projects, dependencies.gatewayConfig ?? new GatewayConfigAdapter(settings.gatewayEnv), settings.dataDir)
  const file = join(settings.dataDir, 'templates.json')
  const templates = new TemplateRepository(file)
  const getTemplate = (id: unknown, projectId: unknown) => {
    projects.get(projectId)
    const item = templates.all().find((t) => t.templateId === id && t.projectId === projectId)
    if (!item) throw new ApiError('模板不存在', 40001, 404)
    return item
  }
  const templatePrefix = 'POST /api/dashboard/profileTemplate/'
  const routes: Routes = {
    [`${templatePrefix}list`]: ({ body }) => {
      projects.get(body.projectId)
      const result = paginate(
        templates.all().filter((t) => t.projectId === body.projectId),
        body,
        'list',
      )
      return { ...result, pages: Math.ceil(result.total / result.size) }
    },
    'GET /api/dashboard/profileTemplate/detail': ({ query }) => getTemplate(query.get('templateId'), query.get('projectId')),
    [`${templatePrefix}create`]: ({ body }) => {
      projects.get(body.projectId)
      if (!body.templateName?.trim()) throw new ApiError('模板名称不能为空')
      const item = { ...body, templateId: randomUUID(), bindingCount: 0, updatedAt: new Date().toISOString() } as StoredTemplate
      templates.replace([...templates.all(), item])
      return item
    },
    [`${templatePrefix}update`]: ({ body }) => {
      const item = getTemplate(body.templateId, body.projectId)
      const next = {
        ...item,
        ...body,
        templateId: item.templateId,
        projectId: item.projectId,
        bindingCount: item.bindingCount,
        updatedAt: new Date().toISOString(),
      }
      templates.replace(templates.all().map((r) => (r.templateId === item.templateId ? next : r)))
      return next
    },
    [`${templatePrefix}delete`]: ({ body }) => {
      const item = getTemplate(body.templateId, body.projectId)
      templates.replace(templates.all().filter((t) => t.templateId !== item.templateId))
      return { ok: true }
    },
    'POST /api/dashboard/keys/list': ({ body }) => keys.list(body),
    'POST /api/dashboard/keys/create': ({ body }) => keys.create(body),
    'POST /api/dashboard/keys/copy': ({ body }) => keys.copy(body.apiKeyId),
    'POST /api/dashboard/keys/revoke/{apiKeyId}': ({ params }) => keys.revoke(params[0]),
    'GET /api/user/quota/summary': () => ({
      memoryCount: 0,
      knowledgebaseCount: 0,
      knowledgebaseCapacity: 0,
      allowedKnowledgebaseCount: 0,
    }),
    'POST /api/user/subscription/details': () => ({ cycles: [], currentCycle: { id: '' } }),
    'POST /api/dashboard/skill/skillMD': () => '',
  }
  for (const name of ['list', 'file/list', 'memory/list'])
    routes[`POST /api/dashboard/knowledgebase/${name}`] = () => ({ records: [], total: 0 })
  routes['POST /api/dashboard/knowledgebase/detail'] = () => ({})
  for (const name of ['create', 'update', 'project/associate', 'project/remove'])
    routes[`POST /api/dashboard/knowledgebase/${name}`] = () => null
  return routes
}
