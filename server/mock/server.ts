/** 独立跑的 mock 后端：真 HTTP 服务，实现根目录 openapi.json 里那套接口。
 *
 *  跑法: pnpm mock          （默认 5280）
 *        pnpm dev:all       （同时起 mock 与前端）
 */

import http from 'node:http'
import { COLLECTIONS, KEY_PREFIX, TOOLS, iso, newId, newSecret, type Row } from './data.ts'
import { load, save } from './store.ts'

const PORT = Number(process.env.MOCK_PORT || 5280)
const DB_FILE = process.env.MOCK_DB || '.data/mock-db.json'

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type,Authorization',
}

function json(res: http.ServerResponse, status: number, body: unknown) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', ...CORS })
  res.end(JSON.stringify(body))
}

/** 成功响应：code === 0 时前端取 data */
const ok = (res: http.ServerResponse, data: unknown) => json(res, 200, { code: 0, data, message: 'success' })

/** 失败响应：错误码见 openapi.json 里那张表 */
function fail(res: http.ServerResponse, code: number, message: string, status = 200) {
  json(res, status, { code, data: null, message })
}

function readBody(req: http.IncomingMessage): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    // Decode once: a UTF-8 character may span multiple network chunks.
    const chunks: Buffer[] = []
    req.on('data', (c: Buffer) => chunks.push(c))
    req.on('end', () => {
      try {
        const raw = Buffer.concat(chunks).toString('utf8')
        const value = raw ? JSON.parse(raw) : {}
        if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('object required')
        resolve(value)
      } catch {
        reject(new HttpError(40000, '请求体必须为合法 JSON 对象'))
      }
    })
  })
}

const db = load(DB_FILE)

/* ------------------------------------------------------------ 小工具 */

const str = (v: unknown, fallback = '') => (v === undefined || v === null ? fallback : String(v))
const num = (v: unknown, fallback: number) => {
  const n = Number(v)
  return Number.isFinite(n) && n > 0 ? n : fallback
}

/** 分页切片，返回切片与总数 */
function paginate<T>(rows: T[], pageNum: number, pageSize: number) {
  const start = (pageNum - 1) * pageSize
  return { items: rows.slice(start, start + pageSize), total: rows.length }
}

const byProject = (rows: Row[], projectId: string) => (projectId ? rows.filter((r) => !r.projectId || r.projectId === projectId) : rows)

const descBy = (field: string) => (a: Row, b: Row) => (String(a[field]) < String(b[field]) ? 1 : -1)

function filterTime(rows: Row[], body: Record<string, unknown>) {
  if (!body.startTime && !body.endTime) return rows
  const low = body.startTime ? Date.parse(String(body.startTime)) : -Infinity
  const high = body.endTime ? Date.parse(String(body.endTime)) : Infinity
  if (Number.isNaN(low) || Number.isNaN(high) || low > high) throw new HttpError(40000, '时间范围无效')
  return rows.filter((r) => {
    const time = Date.parse(String(r.createTime))
    return time >= low && time <= high
  })
}

// The contract's opaque id is an object; templateId remains the stable string identifier.
const publicTemplate = (row: Row) => ({ ...row, id: {} })

/* -------------------------------------------------------------- 路由 */

interface Ctx {
  body: Record<string, unknown>
  query: URLSearchParams
  /** 路径里的 :参数 */
  params: Record<string, string>
  url: URL
}

type Handler = (ctx: Ctx) => unknown

const ROUTES: Record<string, Handler> = {
  /* ---------------------------------------------------- 项目（工作区） */

  'GET /api/dashboard/project/dict': ({ query }) => {
    // 按语言返回名字，这里只有中文，原样返回
    void query
    return db.projects.map((p) => ({ id: p.id, name: p.name, status: p.status }))
  },

  'POST /api/dashboard/project/list': ({ body }) => {
    const { items, total } = paginate(db.projects, num(body.pageNum, 1), num(body.pageSize, 10))
    return { records: items, total }
  },

  'POST /api/dashboard/project/create': ({ body }) => {
    if (typeof body.name !== 'string') throw new HttpError(40000, '项目名称必须为字符串')
    const name = str(body.name).trim()
    if (!name) throw new HttpError(40000, '项目名称不能为空')
    if (db.projects.some((p) => p.name === name)) throw new HttpError(40000, '已存在同名项目')
    const row: Row = {
      id: newId('proj'),
      name,
      description: str(body.description),
      isMultiView: Number(body.isMultiView) ? 1 : 0,
      status: 'DISABLED',
      createTime: iso(new Date()),
    }
    db.projects.push(row)
    save(DB_FILE, db)
    return row
  },

  'POST /api/dashboard/project/update': ({ body }) => {
    const row = db.projects.find((p) => p.id === body.id)
    if (!row) throw new HttpError(40001, '项目不存在')
    // 切换当前项目：body 只有 { id, status }；编辑：body 带 name
    if (body.name !== undefined) {
      row.name = str(body.name).trim()
      row.description = str(body.description)
      row.isMultiView = Number(body.isMultiView) ? 1 : 0
    }
    if (body.status !== undefined) {
      if (body.status === 'ENABLED') for (const p of db.projects) p.status = 'DISABLED'
      row.status = str(body.status)
    }
    save(DB_FILE, db)
    return row
  },

  'POST /api/dashboard/project/revoke/:id': ({ params }) => {
    const i = db.projects.findIndex((p) => p.id === params.id)
    if (i < 0) throw new HttpError(40001, '项目不存在')
    db.projects.splice(i, 1)
    save(DB_FILE, db)
    return { ok: true }
  },

  /* ------------------------------------------------------------ 密钥 */

  'POST /api/dashboard/keys/list': ({ body }) => {
    const rows = byProject(db.keys, str(body.projectId)).sort(descBy('createTime'))
    const { items, total } = paginate(rows, num(body.pageNum, 1), num(body.pageSize, 10))
    return { records: items.map(publicKey), total }
  },

  'POST /api/dashboard/keys/create': ({ body }) => {
    const projectId = str(body.projectId)
    const keyName = str(body.keyName).trim()
    if (!projectId) throw new HttpError(40000, '缺少 projectId')
    if (!keyName) throw new HttpError(40000, '密钥名称不能为空')
    const row: Row = { id: newId('key'), projectId, keyName, apiKey: newSecret(), createTime: iso(new Date()) }
    db.keys.push(row)
    save(DB_FILE, db)
    return publicKey(row)
  },

  'POST /api/dashboard/keys/revoke/:id': ({ params }) => {
    const i = db.keys.findIndex((k) => k.id === params.id)
    if (i < 0) throw new HttpError(40001, '密钥不存在')
    db.keys.splice(i, 1)
    save(DB_FILE, db)
    return { ok: true }
  },

  'POST /api/dashboard/keys/copy': ({ body }) => {
    const row = db.keys.find((k) => k.id === body.apiKeyId)
    if (!row) throw new HttpError(40001, '密钥不存在')
    return row.apiKey
  },

  /* -------------------------------------------------------- 调用日志 */

  'POST /api/dashboard/log/list': ({ body }) => {
    let rows = filterTime(byProject(db.logs, str(body.projectId)), body)
    const q = str(body.query).toLowerCase()
    if (q) rows = rows.filter((r) => String(r.traceId).toLowerCase().includes(q) || String(r.userId).toLowerCase().includes(q))
    if (body.eventType) rows = rows.filter((r) => r.eventType === body.eventType)
    if (body.status) rows = rows.filter((r) => r.status === body.status)
    if (body.subjectType && body.subjectType !== 'all' && body.subjectId) {
      const id = str(body.subjectId)
      rows = rows.filter((r) => (body.subjectType === 'user' ? r.userId === id : r.agentId === id))
    }
    rows = [...rows].sort(descBy('createTime'))
    // 列表响应在这个接口叫 logs，不是 records
    const { items, total } = paginate(rows, num(body.pageNum, 1), num(body.pageSize, 50))
    return { logs: items.map(listLog), total }
  },

  'POST /api/dashboard/log/detail': ({ body }) => {
    const row = db.logs.find((l) => l.id === body.logId)
    if (!row) throw new HttpError(40001, '日志不存在')
    return row
  },

  /* ------------------------------------------------------------ 记忆 */

  'POST /api/dashboard/project/memory/list': ({ body }) => {
    let rows = filterTime(byProject(db.memories, str(body.projectId)), body)
    if (body.memoryDetailType) rows = rows.filter((r) => r.memoryDetailType === body.memoryDetailType)
    if (body.knowledgeBaseId) rows = rows.filter((r) => r.knowledgeBaseId === body.knowledgeBaseId)
    if (body.externalUserId) rows = rows.filter((r) => r.externalUserId === body.externalUserId)
    if (body.subjectType) rows = rows.filter((r) => r.subjectType === body.subjectType)
    const q = str(body.memSearchContent).toLowerCase()
    if (q)
      rows = rows.filter(
        (r) =>
          String(r.memoryContent).toLowerCase().includes(q) ||
          String(r.skillName ?? '')
            .toLowerCase()
            .includes(q),
      )
    rows = [...rows].sort(descBy('createTime'))
    const pageNum = num(body.pageNum, 1)
    const pageSize = num(body.pageSize, 50)
    const { items, total } = paginate(rows, pageNum, pageSize)
    // 列表响应在这个接口叫 memListItemResponseList，分页字段是 current
    return { memListItemResponseList: items.map(publicMemory), total, current: pageNum }
  },

  'POST /api/dashboard/project/memory/detail': ({ body }) => {
    const row = db.memories.find((m) => m.memoryId === body.memoryId || m.id === body.memoryId)
    if (!row) throw new HttpError(40001, '记忆不存在')
    return publicMemory(row)
  },

  'POST /api/dashboard/project/memory/delete': ({ body }) => {
    const ids = Array.isArray(body.memory_ids) ? body.memory_ids.map(String) : []
    const before = db.memories.length
    db.memories = db.memories.filter((m) => !ids.includes(String(m.memoryId)) && !ids.includes(String(m.id)))
    save(DB_FILE, db)
    return { deleted: before - db.memories.length }
  },

  /* ---------------------------------------------------------- 知识库 */

  'POST /api/dashboard/knowledgebase/list': ({ body }) => {
    let rows = [...db.knowledgeBases]
    const name = str(body.name).toLowerCase()
    if (name) rows = rows.filter((k) => String(k.knowledgebaseName).toLowerCase().includes(name))
    if (body.projectId) rows = rows.filter((k) => (k.projectIds as string[])?.includes(str(body.projectId)))
    const { items, total } = paginate(rows, num(body.pageNum, 1), num(body.pageSize, 10))
    return { records: items, total }
  },

  'POST /api/dashboard/knowledgebase/create': ({ body }) => {
    const name = str(body.knowledgebaseName).trim()
    if (!name) throw new HttpError(40000, '知识库名称不能为空')
    const row: Row = {
      id: newId('kb'),
      kbId: '',
      knowledgebaseName: name,
      knowledgebaseDescription: str(body.knowledgebaseDescription),
      projectIds: Array.isArray(body.projectIds) ? body.projectIds.map(String) : [],
      createTime: iso(new Date()),
    }
    row.kbId = row.id
    db.knowledgeBases.push(row)
    save(DB_FILE, db)
    return row
  },

  'POST /api/dashboard/knowledgebase/update': ({ body }) => {
    const row = db.knowledgeBases.find((k) => k.kbId === body.id || k.id === body.id)
    if (!row) throw new HttpError(40001, '知识库不存在')
    row.knowledgebaseName = str(body.knowledgebaseName).trim()
    row.knowledgebaseDescription = str(body.knowledgebaseDescription)
    if (Array.isArray(body.projectIds)) row.projectIds = body.projectIds.map(String)
    save(DB_FILE, db)
    return row
  },

  'POST /api/dashboard/knowledgebase/detail': ({ body }) => {
    const row = db.knowledgeBases.find((k) => k.kbId === body.knowledgeBaseId || k.id === body.knowledgeBaseId)
    if (!row) throw new HttpError(40001, '知识库不存在')
    return row
  },

  'POST /api/dashboard/knowledgebase/dict': ({ body }) => {
    const name = str(body.name).toLowerCase()
    const rows = name ? db.knowledgeBases.filter((k) => String(k.knowledgebaseName).toLowerCase().includes(name)) : db.knowledgeBases
    return rows.map((k) => ({ id: k.kbId, name: k.knowledgebaseName }))
  },

  'POST /api/dashboard/knowledgebase/project/associate': ({ body }) => {
    const ids = Array.isArray(body.knowledgeBaseIds) ? body.knowledgeBaseIds.map(String) : []
    const projectId = str(body.projectId)
    for (const k of db.knowledgeBases) {
      const list = (k.projectIds as string[]) ?? []
      const has = list.includes(projectId)
      if (ids.includes(String(k.kbId)) && !has) k.projectIds = [...list, projectId]
      if (!ids.includes(String(k.kbId)) && has) k.projectIds = list.filter((p) => p !== projectId)
    }
    save(DB_FILE, db)
    return { ok: true }
  },

  'POST /api/dashboard/knowledgebase/project/remove': ({ body }) => {
    const projectId = str(body.projectId)
    const kbId = str(body.knowledgeBaseId)
    const row = db.knowledgeBases.find((k) => k.kbId === kbId)
    if (row) row.projectIds = ((row.projectIds as string[]) ?? []).filter((p) => p !== projectId)
    if (body.deleteAll) db.knowledgeBases = db.knowledgeBases.filter((k) => k.kbId !== kbId)
    save(DB_FILE, db)
    return { ok: true }
  },

  'POST /api/dashboard/knowledgebase/file/list': () => ({ records: [], total: 0 }),
  'POST /api/dashboard/knowledgebase/file/delete': () => ({ ok: true }),
  'POST /api/dashboard/knowledgebase/file/log/list': () => ({ records: [], total: 0 }),
  'POST /api/dashboard/knowledgebase/file/log/detail': () => ({}),

  'POST /api/dashboard/knowledgebase/memory/list': ({ body }) => {
    const rows = db.memories.filter((m) => m.knowledgeBaseId === body.knowledgeBaseId)
    const { items, total } = paginate(rows, num(body.pageNum, 1), num(body.pageSize, 10))
    return { records: items.map(publicMemory), total, size: num(body.pageSize, 10) }
  },

  'POST /api/dashboard/knowledgebase/memory/detail': ({ body }) => {
    const row = db.memories.find((m) => m.id === body.updateLogId)
    if (!row) throw new HttpError(40001, '记忆更新记录不存在')
    return publicMemory(row)
  },

  /* -------------------------------------------------------- 属性树模板 */

  'POST /api/dashboard/profileTemplate/list': ({ body }) => {
    const rows = byProject(db.templates, str(body.projectId))
    const pageNum = num(body.pageNum, 1)
    const pageSize = num(body.pageSize, 10)
    const { items, total } = paginate(rows, pageNum, pageSize)
    // 列表响应在这个接口叫 list，且带 pages
    return { list: items.map(publicTemplate), total, current: pageNum, size: pageSize, pages: Math.max(1, Math.ceil(total / pageSize)) }
  },

  'GET /api/dashboard/profileTemplate/detail': ({ query }) => {
    const row = db.templates.find((t) => t.templateId === query.get('templateId'))
    if (!row) throw new HttpError(40001, '模板不存在')
    return publicTemplate(row)
  },

  'POST /api/dashboard/profileTemplate/create': ({ body }) => {
    const name = str(body.templateName).trim()
    if (!name) throw new HttpError(40000, '模板名称不能为空')
    const row: Row = {
      id: newId('tpl'),
      templateId: '',
      templateName: name,
      bindingCount: 0,
      updatedAt: iso(new Date()),
      projectId: str(body.projectId, 'proj_default'),
    }
    row.templateId = row.id
    // 模板字段：body 里除了元信息以外的键都算分组
    for (const [k, v] of Object.entries(body)) if (!META_KEYS.has(k)) row[k] = v
    db.templates.push(row)
    save(DB_FILE, db)
    return row
  },

  'POST /api/dashboard/profileTemplate/update': ({ body }) => {
    const row = db.templates.find((t) => t.templateId === body.templateId)
    if (!row) throw new HttpError(40001, '模板不存在')
    for (const [k, v] of Object.entries(body)) if (k !== 'templateId' && k !== 'projectId') row[k] = v
    row.updatedAt = iso(new Date())
    save(DB_FILE, db)
    return row
  },

  'POST /api/dashboard/profileTemplate/delete': ({ body }) => {
    const i = db.templates.findIndex((t) => t.templateId === body.templateId)
    if (i < 0) throw new HttpError(40001, '模板不存在')
    db.templates.splice(i, 1)
    save(DB_FILE, db)
    return { ok: true }
  },

  /* ------------------------------------------------------------ 用量 */

  'POST /api/dashboard/usage/query': ({ body }) => {
    // 按天聚合调用日志。真后端从审计流水算，这里从数据集算，形状一致
    const byDay = new Map<string, Record<string, { calls?: number; totalTokens?: number }>>()
    for (const r of byProject(db.logs, str(body.projectId))) {
      const day = String(r.createTime).slice(0, 10)
      const bucket = byDay.get(day) ?? {}
      const key = String(r.eventType)
      const cur = bucket[key] ?? { calls: 0, totalTokens: 0 }
      cur.calls = (cur.calls ?? 0) + 1
      cur.totalTokens = (cur.totalTokens ?? 0) + Number(r.requestTokens ?? 0) + Number(r.responseTokens ?? 0)
      bucket[key] = cur
      byDay.set(day, bucket)
    }
    const days: { date: string }[] = []
    const start = new Date(str(body.startDate) || Date.now() - 13 * 86400000)
    const end = new Date(str(body.endDate) || Date.now())
    for (let d = new Date(start); d <= end && days.length < 120; d.setDate(d.getDate() + 1)) {
      days.push({ date: iso(d).slice(0, 10) })
    }
    const dailyStats = days.map(({ date }) => {
      const b = byDay.get(date) ?? {}
      return {
        date,
        addMessage: b.addMessage ?? { calls: 0, totalTokens: 0 },
        searchMemory: b.searchMemory ?? { calls: 0, totalTokens: 0 },
        chat: { requestTokens: b.chat?.totalTokens ?? 0, responseTokens: 0, totalTokens: b.chat?.totalTokens ?? 0 },
        extractMemory: { requestTokens: 0, responseTokens: 0, totalTokens: 0 },
        rerank: { requestTokens: 0, totalTokens: 0 },
      }
    })
    return { dailyStats }
  },

  /* ------------------------------------------------------ 自定义抽取 */

  'POST /api/dashboard/prompt-config/detail': ({ body }) => {
    const row = db.promptConfigs.find((p) => p.projectId === str(body.projectId, 'proj_default')) ?? db.promptConfigs[0]
    return row ?? {}
  },

  'POST /api/dashboard/prompt-config/save': ({ body }) => {
    const projectId = str(body.projectId, 'proj_default')
    const row = db.promptConfigs.find((p) => p.projectId === projectId)
    if (row) {
      Object.assign(row, body, { updatedAt: iso(new Date()) })
    } else {
      db.promptConfigs.push({ id: newId('pc'), projectId, ...body, updatedAt: iso(new Date()) })
    }
    save(DB_FILE, db)
    return { ok: true }
  },

  'POST /api/dashboard/prompt-config/generate': ({ body }) => ({
    prompt: `根据「${str(body.description, '当前描述')}」生成的抽取规则：\n\n1. 只保留跨任务仍然成立的内容\n2. 一次性的任务流水不写入\n3. 个案结论不写入`,
  }),

  'POST /api/dashboard/prompt-config/reset-default': () => ({
    prompt:
      '你是一个记忆抽取器。从下面的对话里抽取值得长期保留的信息。\n\n只保留跨任务、跨会话仍然成立的内容；一次性的任务流水与个案结论不要写入。',
  }),

  'POST /api/dashboard/prompt-config/debug/extract': ({ body }) => ({
    extractionTypes: Array.isArray(body.extractionTypes) ? body.extractionTypes : ['text_mem'],
    results: [
      { type: 'text_mem', content: '用户所在团队负责长期记忆服务的检索链路' },
      { type: 'pref_mem', content: '偏好：结论先行，给出可量化的数字' },
    ],
  }),

  /* -------------------------------------------------------------- 技能 */

  'POST /api/dashboard/skill/skillMD': ({ body }) => `# ${str(body.oss_url, '技能')}\n\n这是一个技能包，包含排查步骤与检查清单。\n`,

  /* -------------------------------------------------------------- 账号 */

  'GET /api/user/quota/summary': () => ({
    memoryCount: db.memories.length,
    knowledgebaseCount: db.knowledgeBases.length,
    knowledgebaseCapacity: 1_000_000,
    allowedKnowledgebaseCount: 10,
  }),

  'POST /api/user/subscription/details': () => ({
    cycles: [{ id: 'cycle_current', cycleStartTime: '2026-10-01', cycleEndTime: '2026-10-31' }],
    currentCycle: { id: 'cycle_current' },
  }),

  /* -------------------------------------------------------------- 模型 */

  'GET /api/dashboard/models': () => ({
    records: [
      { role: '记忆读取', model: 'qwen-max', spec: 'dashscope.aliyuncs.com', source: 'MEMRADER_MODEL', group: 'memory' },
      { role: '记忆写入 / 对话', model: 'qwen-plus', spec: 'dashscope.aliyuncs.com', source: 'MOS_CHAT_MODEL', group: 'chat' },
      { role: '向量化', model: 'text-embedding-v3', spec: '1536 维', source: 'MOS_EMBEDDER_MODEL', group: 'memory' },
      { role: '重排', model: 'bge-reranker-large', spec: '后端 bge', source: 'MOS_RERANKER_MODEL', group: 'memory' },
    ],
    total: 4,
  }),

  /* ------------------------------------------ 图谱 */

  'GET /api/dashboard/graph/memories': ({ query }) => {
    const tenant = str(query.get('tenant'), 'proj_default')
    const list = db.memories.filter((m) => !m.projectId || m.projectId === tenant)
    const GRAPH_TYPES = ['LongTermMemory', 'PreferenceMemory', 'InsightMemory', 'Context', 'UserMemory']
    const nodes = list.map((m, idx) => ({
      id: m.id,
      label: str(m.memoryText || m.content, '记忆节点'),
      labelTruncated: false,
      memoryType: GRAPH_TYPES[idx % GRAPH_TYPES.length],
      createdAt: str(m.createdAt, iso(new Date())),
      sessionId: `session_${idx % 4}`,
      clusterId: `session:session_${idx % 4}`,
      userId: str(m.userId, 'user_default'),
      sourceIds: [],
    }))
    const links: { source: string; target: string; kind: 'session' | 'parent' | 'merged' | 'derived' }[] = []
    for (let i = 1; i < nodes.length; i++) {
      if (nodes[i].sessionId === nodes[i - 1].sessionId) {
        links.push({ source: nodes[i - 1].id, target: nodes[i].id, kind: 'session' })
      }
    }
    const typeCount = new Map<string, number>()
    for (const n of nodes) typeCount.set(n.memoryType, (typeCount.get(n.memoryType) ?? 0) + 1)
    const types = [...typeCount.entries()].map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count)
    return {
      nodes,
      links,
      stats: { nodes: nodes.length, links: links.length, sessions: 4, clusters: 4, orphans: 0, truncated: false, types },
    }
  },

  'GET /api/dashboard/graph/memory': ({ query }) => {
    const id = str(query.get('id'))
    const row = db.memories.find((m) => m.id === id)
    return { id, memory: str(row?.memoryText || row?.content, '记忆全文内容') }
  },

  /* ------------------------------------------ 本控制台的扩展 */

  'GET /api/meta': () => ({
    mode: 'sample',
    // 控制台自带的示例后端；接真后端时由对方返回 mode=live
    service: '',
    health: null,
    keyPrefix: KEY_PREFIX,
    tools: TOOLS,
  }),
}

/** 模板接口里这些键是元信息，其余键都当成分组数据 */
const META_KEYS = new Set(['templateId', 'templateName', 'projectId', 'bindingCount', 'updatedAt', 'id'])

/* ---------------------------------------------------------- 出参整形 */

function publicKey(row: Row) {
  // 列表里就下发完整密钥，前端自己截断展示
  return { id: row.id, keyName: row.keyName, apiKey: row.apiKey, createTime: row.createTime }
}

function listLog(row: Row) {
  return {
    id: row.id,
    createTime: row.createTime,
    eventType: row.eventType,
    userIds: row.userIds,
    agentIds: row.agentIds,
    status: row.status,
    duration: row.duration,
  }
}

function publicMemory(row: Row) {
  const out: Record<string, unknown> = {
    memoryId: row.memoryId,
    memoryContent: row.memoryContent,
    subjectType: row.subjectType,
    externalUserId: row.externalUserId,
    createTime: row.createTime,
  }
  if (row.skillName !== undefined) out.skillName = row.skillName
  if (row.skillUrl !== undefined) out.skillUrl = row.skillUrl
  if (row.profileTemplateId !== undefined) out.profileTemplateId = row.profileTemplateId
  if (row.profileInstanceId !== undefined) out.profileInstanceId = row.profileInstanceId
  return out
}

class HttpError extends Error {
  code: number
  constructor(code: number, message: string) {
    super(message)
    this.code = code
  }
}

/* -------------------------------------------------------------- 分发 */

/** 'POST /api/keys/revoke/:id' 与 '/api/keys/revoke/key_ab' 匹配，返回 { id: 'key_ab' } */
function match(routeKey: string, method: string, path: string): Record<string, string> | null {
  const [rm, ...rparts] = routeKey.split(' ')
  if (rm !== method) return null
  const rpath = rparts.join(' ').split('/')
  const ppath = path.split('/')
  if (rpath.length !== ppath.length) return null
  const params: Record<string, string> = {}
  for (let i = 0; i < rpath.length; i++) {
    if (rpath[i].startsWith(':')) params[rpath[i].slice(1)] = decodeURIComponent(ppath[i])
    else if (rpath[i] !== ppath[i]) return null
  }
  return params
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url || '/', `http://localhost:${PORT}`)
  const method = req.method === 'OPTIONS' ? 'OPTIONS' : req.method === 'GET' ? 'GET' : 'POST'

  if (req.method === 'OPTIONS') return json(res, 204, null)

  let body: Record<string, unknown>
  try {
    body = await readBody(req)
    for (const key of ['pageNum', 'pageSize'])
      if (body[key] !== undefined && (typeof body[key] !== 'number' || !Number.isInteger(body[key]) || Number(body[key]) < 1))
        throw new HttpError(40000, `${key}必须为正整数`)
    if (
      body.memoryDetailType !== undefined &&
      !['text_mem', 'pref_mem', 'event_mem', 'profile_mem', 'skill_mem', 'tool_mem'].includes(String(body.memoryDetailType))
    )
      throw new HttpError(40000, '未知记忆类型')
  } catch (error) {
    return fail(res, 40000, error instanceof Error ? error.message : '请求参数无效', 400)
  }

  for (const [key, handler] of Object.entries(ROUTES)) {
    const params = match(key, method, url.pathname)
    if (!params) continue
    try {
      return ok(res, handler({ body, query: url.searchParams, params, url }))
    } catch (e) {
      if (e instanceof HttpError) return fail(res, e.code, e.message)
      console.error(`[${key}]`, e)
      return fail(res, 50000, e instanceof Error ? e.message : '内部错误', 500)
    }
  }

  fail(res, 40001, `接口不存在：${method} ${url.pathname}`, 404)
})

server.on('error', (e: NodeJS.ErrnoException) => {
  if (e.code === 'EADDRINUSE') {
    console.error(`端口 ${PORT} 已被占用。换一个：MOCK_PORT=5281 pnpm mock`)
    process.exit(1)
  }
  throw e
})

server.listen(PORT, () => {
  const tables = Object.keys(COLLECTIONS).length
  console.log(`mock 后端在 http://localhost:${PORT}（${Object.keys(ROUTES).length} 个接口，${tables} 张表，落盘 ${DB_FILE}）`)
})
