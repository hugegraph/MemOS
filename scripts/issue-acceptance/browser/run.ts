/** Read-only by default; explicit owned-fixture opt-ins permit deletion/selection changes. */
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp, readFile, rm, mkdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { randomUUID } from 'node:crypto'
import { workspaceCases } from './workspaces.ts'
import { performanceCase } from './performance.ts'
import { auditCase } from './audit.ts'
import { mockIndependence } from './mock-independence.ts'
import { withUpstreamStopped } from './upstream-lifecycle.ts'
import { verifyBootstrapRejection, isBootstrapCandidate } from './bootstrap-policy.ts'
import { tableLoading, tableError, tableEmpty, outcome } from './current-ui.ts'
import { clickNative } from './native-input.ts'
import { modelsCase } from './models.ts'
import '../verify-frontend-unchanged.ts'
import type { ChildProcessByStdio } from 'node:child_process'
import type { Readable } from 'node:stream'
import type { ClickPoint, Send, Evaluate, NetworkRecord, ApiResponse, DictionaryEvidence } from './types.ts'
import type { Check, ToolError, MemoryPage, UsageResult, LogPage, NativeBuckets, Envelope } from '../../tool-types.ts'
import type { Memory, LogDetail } from '../../../shared/contract.ts'
import type { DashboardInput } from '../../../server/live/types.ts'

const rows: (Check & { id: string; requirement: string; scope: string })[] = []
const output = resolve(process.env.ISSUE_REPORT_DIR || '.data/issue-acceptance')
const base = process.env.ISSUE_WEB_BASE!
const apiBase = process.env.ISSUE_API_BASE || base
const executable = process.env.ISSUE_CHROME!
const selectedCases = process.env.ISSUE_CASES ? new Set(process.env.ISSUE_CASES.split(',')) : null
if (process.env.ISSUE_WORKSPACE_MANIFEST && selectedCases) selectedCases.add('UI-WORKSPACE-RESTORE')
const timeout = 15000
let browser: ChildProcessByStdio<null, null, Readable> | undefined, profile: string | undefined, socket: WebSocket | undefined
let send!: Send, evaluate!: Evaluate
let startupDiagnostics = ''
let lastClick: ClickPoint | null = null
let serial = 0
const pending = new Map<
    number,
    { ok: (value: unknown) => void; fail: (error: Error) => void; timer: ReturnType<typeof setTimeout>; method: string }
  >(),
  requests = new Map<string, NetworkRecord>(),
  runtimeErrors: number[] = []
const loadedDocuments = new Set<string>()
const pages = [
  ['quickstart', '快速开始', 'required'],
  ['apikeys', '接口密钥', 'bonus'],
  ['requests', '调用日志', 'required'],
  ['knowledgeBase', '知识库', 'required'],
  ['memoryList', '记忆列表', 'required'],
  ['models', '模型列表', 'required'],
  ['profileTemplate', '画像模板', 'bonus'],
  ['customExtraction', '自定义抽取', 'bonus'],
  ['usage', '用量监控', 'required'],
  ['docs', '接口文档', 'required'],
  ['about', '关于', 'required'],
]
const tabs = ['事实记忆', '偏好记忆', '事件记忆', '用户记忆', '技能', '工具记忆']
const planned = [
  ...pages.map(([route, , requirement]) => ({ id: `UI-PAGE-${route}`, requirement })),
  ...tabs.map((label) => ({ id: `UI-TAB-${label}`, requirement: 'required' })),
  ...[
    'UI-LIVE',
    'UI-MODELS-CONTENT',
    'UI-EMPTY',
    'UI-MEMORY-BYTES',
    'UI-LOADING',
    'UI-ERROR',
    'UI-PROJECT-INITIALIZATION',
    'UI-PROJECT-RETRY',
    'UI-MEMORY-FILTER',
    'UI-USAGE-CONTROLS',
    'UI-MEMORY-DETAIL',
    'UI-USAGE-RANGES',
    'UI-USAGE-TOTALS',
    'UI-DELETE-REAL',
    'UI-PROJECT-SWITCH',
    'UI-NATIVE-TABS-NONEMPTY',
    'UI-LOG-CORRELATION',
    'UI-LOG-10000',
    'UI-STOP-UPSTREAM',
    'UI-STOP-MOCK',
  ].map((id) => ({ id, requirement: 'required' })),
  { id: 'UI-TEMPLATE-DETAIL', requirement: 'bonus' },
]
let intercept: 'hold' | 'fail' | null = null
const paused = new Set<string>()

async function waitFor(fn: () => unknown | Promise<unknown>, description: string, timeoutMs = timeout) {
  const end = Date.now() + timeoutMs
  while (Date.now() < end) {
    if (await fn()) return
    await delay(100)
  }
  throw new Error(`Timed out: ${description}`)
}
async function test(id: string, requirement: string, scope: string, operation: () => Promise<unknown>) {
  if (selectedCases && !selectedCases.has(id)) return
  const start = Date.now()
  try {
    const evidence = await operation()
    rows.push({ id, requirement, scope, status: 'PASS', ms: Date.now() - start, evidence })
  } catch (error) {
    const documentState = (evaluate as Evaluate | undefined)
      ? await evaluate(
          `({path:location.pathname,ready:document.readyState,visible:document.visibilityState,focused:document.hasFocus(),rootLength:document.querySelector('#root')?.innerText.length??0,dialogs:document.querySelectorAll('[role="dialog"]').length})`,
        ).catch(() => null)
      : null
    rows.push({
      id,
      requirement,
      scope,
      status: id === 'UI-LIVE' && (error as ToolError).badgeWaived ? 'WAIVED' : (error as ToolError).blocked ? 'BLOCKED' : 'FAIL',
      waiver: id === 'UI-LIVE' && (error as ToolError).badgeWaived ? 'missing-live-badge' : undefined,
      reason: (error as Error).message,
      ms: Date.now() - start,
      lastClick,
      documentState,
      network: [...requests.values()]
        .filter((r) => r.started >= start && r.path.startsWith('/api/'))
        .slice(-24)
        .map(({ path, status, hasProjectId, memoryType, pageSize, requestFields, done, failed, networkError }) => ({
          path,
          status,
          done,
          failed,
          networkError,
          hasProjectId,
          memoryType,
          pageSize,
          requestFields,
        })),
    })
  }
}
function blocked(reason: string): never {
  throw Object.assign(new Error(reason), { blocked: true })
}
async function apiResponsesSince<T = unknown>(time: number, filter: (r: NetworkRecord) => boolean = () => true): Promise<ApiResponse<T>[]> {
  try {
    await waitFor(() => {
      const current = [...requests.values()].filter((r) => r.started >= time && r.path.startsWith('/api/'))
      return (
        current.length > 0 && current.every((r) => r.done || r.failed) && Date.now() - Math.max(...current.map((r) => r.started)) >= 300
      )
    }, 'API activity settled')
  } catch (error) {
    const states = [...requests.values()]
      .filter((r) => r.started >= time && r.path.startsWith('/api/'))
      .slice(0, 8)
      .map(({ path, origin, status, done, failed, networkError }) => ({ path, origin, status, done, failed, networkError }))
    throw new Error(`${(error as Error).message}; network states=${JSON.stringify(states)}`, { cause: error })
  }
  const all = [...requests.values()].filter((r) => r.started >= time && r.path.startsWith('/api/'))
  const values = all.filter(filter)
  assert.ok(values.length, 'Page did not issue any API request')
  await waitFor(() => values.every((r) => r.done || r.failed), 'API completion')
  async function decode(r: NetworkRecord) {
    if (r.envelope) return
    assert.ok(
      [new URL(base).origin, new URL(apiBase).origin].includes(r.origin),
      'Unexpected API origin; explicitly select ISSUE_API_BASE for cross-origin deployments',
    )
    assert.ok(!r.failed, `Network failure: ${r.path}`)
    const body = await send('Network.getResponseBody', { requestId: r.id })
    const decoded = body.base64Encoded ? Buffer.from(body.body, 'base64').toString() : body.body
    const envelope = JSON.parse(decoded)
    r.bytes = Buffer.byteLength(decoded)
    // Response content is kept in memory only. Never persist keys, log bodies or prompts.
    r.data = envelope.data
    r.envelope = envelope
  }
  // Decode the whole settled document batch: a rejected initial request cannot
  // pass without an independent successful request for the selected workspace.
  for (const r of all) await decode(r)
  for (const r of values) {
    if (r.status !== 200 || r.envelope!.code !== 0) {
      assert.ok(isBootstrapCandidate(r), `${r.method} ${r.path}: HTTP ${r.status}, business code ${r.envelope!.code}`)
      const dictionary = all.find((d) => d.path === '/api/dashboard/project/dict' && d.loaderId === r.loaderId)
      assert.ok(dictionary, `No dictionary evidence for failure: ${r.path}`)
      r.bootstrapRejection = verifyBootstrapRejection(r, r.envelope!, dictionary as DictionaryEvidence, all)
    }
  }
  const successful = values.filter((r) => !r.bootstrapRejection)
  assert.ok(successful.length, 'Rejected requests alone do not prove functional success')
  return successful as ApiResponse<T>[]
}
async function navigate(path: string, { waitForIdle = true } = {}) {
  await send('Page.bringToFront')
  const started = Date.now()
  const navigation = await send('Page.navigate', { url: `${base}${path}` })
  assert.ok(!navigation.errorText, 'Navigation failed')
  // A same-URL reload can leave the old document ready while the next one is loading.
  // Waiting only on pathname/readyState clicks the old page and loses the interaction.
  if (navigation.loaderId) await waitFor(() => loadedDocuments.has(navigation.loaderId!), 'new document load')
  await waitFor(
    () =>
      evaluate(
        `location.pathname === ${JSON.stringify(path)} && document.readyState === 'complete' && document.querySelector('#root')?.innerText.length > 50`,
      ),
    'page content',
  )
  if (waitForIdle) {
    await apiResponsesSince(started)
    await waitFor(() => evaluate(`!(${tableLoading}) && !(${tableError})`), 'selected-scope page resolved')
  }
  return started
}
async function click(expression: string) {
  return clickNative(
    {
      send,
      evaluate,
      waitFor,
      observe: (point) => {
        lastClick = point
      },
    },
    expression,
  )
}
async function setup() {
  if (!base || !executable) blocked('Set ISSUE_WEB_BASE and ISSUE_CHROME explicitly; no deployment/browser was selected')
  const url = new URL(base)
  assert.ok(
    ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && !url.search,
    'Use a credential-free HTTP(S) origin',
  )
  let meta
  try {
    meta = await (await fetch(`${apiBase}/api/meta`, { signal: AbortSignal.timeout(5000), redirect: 'error' })).json()
  } catch {
    blocked('Selected frontend/BFF is unreachable or returned non-JSON; no browser was launched')
  }
  assert.equal(meta.code, 0, 'Meta preflight failed')
  assert.equal(meta.data?.mode, 'live', 'Browser acceptance refuses sample/mock mode')
  const apiUrl = new URL(apiBase)
  assert.ok(['http:', 'https:'].includes(apiUrl.protocol) && !apiUrl.username && !apiUrl.password && !apiUrl.search)
  if (apiUrl.origin !== url.origin) {
    const apiMeta = await (await fetch(`${apiUrl.origin}/api/meta`, { signal: AbortSignal.timeout(5000), redirect: 'error' })).json()
    assert.equal(apiMeta.code, 0)
    assert.equal(apiMeta.data?.mode, 'live', 'Explicit API deployment must also be live')
  }
  profile = await mkdtemp(join(tmpdir(), 'memos-acceptance-browser-'))
  // Visible interactive validation is opt-in; CI remains headless. Never use the user's normal profile.
  browser = spawn(
    executable,
    [
      ...(process.env.ISSUE_HEADFUL === '1' ? [] : ['--headless=new']),
      '--remote-debugging-address=127.0.0.1',
      '--remote-debugging-port=0',
      `--user-data-dir=${profile}`,
      '--no-first-run',
      '--no-default-browser-check',
      '--force-device-scale-factor=1',
      '--disable-features=CalculateNativeWinOcclusion',
      '--disable-backgrounding-occluded-windows',
      '--disable-background-timer-throttling',
      '--disable-renderer-backgrounding',
      '--window-size=1440,1000',
      'about:blank',
    ],
    { windowsHide: process.env.ISSUE_HEADFUL !== '1', stdio: ['ignore', 'ignore', 'pipe'] },
  )
  // Until CDP connects the only page is about:blank, so these are startup diagnostics, not page data.
  browser.stderr.on('data', (chunk) => {
    if (!socket) startupDiagnostics = (startupDiagnostics + chunk.toString()).slice(-4000)
  })
  let launchError: NodeJS.ErrnoException | undefined
  browser.on('error', (error) => {
    launchError = error
  })
  let port
  await waitFor(async () => {
    if (launchError) blocked(`Browser launch failed: ${launchError.code}`)
    // Some Windows browser launchers exit 0 after handing off to their child.
    // Only a nonzero exit is conclusive; successful readiness is the profile's CDP file.
    if (browser!.exitCode !== null && browser!.exitCode !== 0)
      blocked(
        `Browser exited during startup (code=${browser!.exitCode}, signal=${browser!.signalCode}): ${startupDiagnostics.slice(-1200)}`,
      )
    try {
      port = (await readFile(join(profile!, 'DevToolsActivePort'), 'utf8')).split('\n')[0]
      return Boolean(port)
    } catch {
      return false
    }
  }, 'CDP endpoint')
  const targets = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()) as {
    type: string
    url: string
    webSocketDebuggerUrl: string
  }[]
  const target = targets.find((t) => t.type === 'page' && t.url === 'about:blank')
  assert.ok(target, 'Dedicated browser target missing')
  socket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((ok, fail) => {
    socket!.onopen = ok
    socket!.onerror = fail
  })
  send = (method, params = {}) =>
    new Promise((ok, fail) => {
      const id = ++serial
      const timer = setTimeout(() => {
        pending.delete(id)
        fail(new Error(`CDP timeout: ${method}`))
      }, timeout)
      pending.set(id, { ok: ok as (value: unknown) => void, fail, timer, method })
      socket!.send(JSON.stringify({ id, method, params }))
    })
  socket.onmessage = (event) => {
    const msg = JSON.parse(event.data)
    if (msg.id) {
      const item = pending.get(msg.id)
      if (!item) return
      clearTimeout(item.timer)
      pending.delete(msg.id)
      if (msg.error) item.fail(new Error(`CDP ${item.method} failed: ${msg.error.code} ${String(msg.error.message).slice(0, 200)}`))
      else item.ok(msg.result)
      return
    }
    const p = msg.params
    if (msg.method === 'Page.lifecycleEvent' && p.name === 'load') loadedDocuments.add(p.loaderId)
    if (msg.method === 'Runtime.exceptionThrown') runtimeErrors.push(Date.now())
    if (msg.method === 'Network.requestWillBeSent' && /^https?:/.test(p.request.url) && p.request.method !== 'OPTIONS') {
      let memoryType: string | undefined,
        projectId: string | undefined,
        pageSize: number | undefined,
        deleteIds: string[] | undefined,
        requestFields: Record<string, string> | undefined,
        requestBody: DashboardInput | undefined,
        hasProjectId = false
      try {
        const body: DashboardInput = JSON.parse(p.request.postData || '{}')
        requestBody = body
        requestFields = Object.fromEntries(
          Object.entries(body)
            .slice(0, 24)
            .map(([key, value]) => [key, value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value]),
        )
        memoryType = body.memoryDetailType
        projectId = body.projectId
        pageSize = body.pageSize
        if (new URL(p.request.url).pathname.endsWith('/project/memory/delete')) deleteIds = body.memory_ids
        hasProjectId = typeof body.projectId === 'string' && body.projectId.length > 0
      } catch {
        /* non-JSON bodies are not needed */
      }
      const url = new URL(p.request.url)
      const projectScoped =
        /\/(?:project\/memory\/list|prompt-config\/detail|log\/list|usage\/query|keys\/list|profileTemplate\/list|knowledgebase\/list)$/.test(
          url.pathname,
        )
      requests.set(p.requestId, {
        id: p.requestId,
        path: url.pathname,
        origin: url.origin,
        method: p.request.method,
        loaderId: p.loaderId,
        body: requestBody,
        memoryType,
        projectId,
        pageSize,
        deleteIds,
        requestFields,
        hasProjectId,
        projectScoped,
        started: Date.now(),
      })
    }
    const r = requests.get(p?.requestId)
    if (r && msg.method === 'Network.responseReceived') r.status = p.response.status
    if (r && msg.method === 'Network.loadingFinished') {
      r.done = true
      r.finished = Date.now()
    }
    if (r && msg.method === 'Network.loadingFailed') {
      r.failed = true
      r.networkError = p.errorText
    }
    if (msg.method === 'Fetch.requestPaused') {
      if (intercept === 'hold') paused.add(p.requestId)
      else if (intercept === 'fail') {
        void send('Fetch.fulfillRequest', {
          requestId: p.requestId,
          responseCode: 502,
          responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
          body: Buffer.from(JSON.stringify({ code: 50200, data: null, message: 'Acceptance simulated upstream unavailable' })).toString(
            'base64',
          ),
        }).catch(() => {})
      } else void send('Fetch.continueRequest', { requestId: p.requestId }).catch(() => {})
    }
  }
  evaluate = async (expression) => {
    const value = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    if (value.exceptionDetails) throw new Error('Browser expression failed')
    return value.result.value
  }
  await send('Page.enable')
  await send('Page.setLifecycleEventsEnabled', { enabled: true })
  await send('Runtime.enable')
  await send('Network.enable')
  await send('Network.setCacheDisabled', { cacheDisabled: true })
  await send('Page.addScriptToEvaluateOnNewDocument', { source: `localStorage.setItem('console.lang','zh')` })
}

try {
  await setup()
  await test('UI-PROJECT-INITIALIZATION', 'required', 'browser-simulated-network', async () => {
    const started = Date.now()
    intercept = 'hold'
    await send('Fetch.enable', { patterns: [{ urlPattern: '*api/dashboard/project/dict*', requestStage: 'Request' }] })
    try {
      await send('Page.navigate', { url: `${base}/cn/memoryList` })
      await waitFor(() => paused.size > 0, 'project dictionary held')
      await waitFor(() => evaluate(tableLoading), 'initial loading skeleton while project dictionary is held')
      const initial = [...requests.values()].filter((r) => r.started >= started && r.projectScoped)
      assert.equal(initial.length, 0, 'Scoped reads must wait for project readiness; rejected early reads are not success')
      assert.ok(await evaluate(`document.querySelectorAll('a[href^="/cn/"]').length >= 5`), 'Original shell remains visible')
    } finally {
      intercept = null
      for (const id of paused) await send('Fetch.continueRequest', { requestId: id }).catch(() => {})
      paused.clear()
      await send('Fetch.disable')
    }
    await waitFor(() => evaluate(`document.querySelector('tbody') && !(${tableLoading})`), 'memory page after project ready')
    await apiResponsesSince(started)
    assert.ok(
      [...requests.values()].filter((r) => r.started >= started && r.projectScoped && r.status === 200).every((r) => r.hasProjectId),
    )
    return 'Scoped reads deferred until project dictionary ready; real selected-scope reads subsequently succeed'
  })
  await test('UI-PROJECT-RETRY', 'required', 'browser-simulated-network', async () => {
    const started = Date.now()
    intercept = 'fail'
    await send('Fetch.enable', { patterns: [{ urlPattern: '*api/dashboard/project/dict*', requestStage: 'Request' }] })
    try {
      await send('Page.navigate', { url: `${base}/cn/memoryList` })
      await waitFor(
        () => evaluate(`(${tableError}) && document.querySelectorAll('a[href^="/cn/"]').length >= 5`),
        'original error state without blank shell',
      )
      const initial = [...requests.values()].filter((r) => r.started >= started && r.projectScoped)
      assert.ok(initial.length > 0 && initial.every((r) => !r.hasProjectId))
    } finally {
      intercept = null
      await send('Fetch.disable')
    }
    const retry = await navigate('/cn/memoryList')
    await waitFor(
      () => evaluate(`document.querySelector('tbody') && !document.querySelector('[role="alert"]')`),
      'retry restores memory page',
    )
    await apiResponsesSince(retry)
    return 'Original frontend: dictionary failure retains shell/error; normal browser refresh after recovery restores real data (no added retry button)'
  })
  for (const [route, title, requirement] of pages)
    await test(`UI-PAGE-${route}`, requirement, 'browser-real', async () => {
      const start = await navigate(`/cn/${route}`)
      assert.ok(await evaluate(`document.body.innerText.includes(${JSON.stringify(title)})`), 'Expected page title missing')
      assert.ok(await evaluate(`!(${tableError})`), 'Page displays an API failure')
      const responses = await apiResponsesSince(start)
      assert.equal(runtimeErrors.filter((t) => t >= start).length, 0, 'Uncaught browser exception')
      return { apiResponses: responses.length, uncaughtExceptions: 0 }
    })
  await test('UI-MODELS-CONTENT', 'required', 'browser-real', () =>
    modelsCase({ apiBase, output, test, navigate, click, evaluate, waitFor, send, apiResponsesSince, requests, blocked }),
  )
  await test('UI-LIVE', 'required', 'browser-real', async () => {
    const start = await navigate('/cn/memoryList')
    const metas = await apiResponsesSince<{ mode: string }>(start, (r) => r.path === '/api/meta')
    assert.equal(metas.at(-1)!.data.mode, 'live', 'The label waiver never waives actual live mode')
    assert.ok(await evaluate(`!document.body.innerText.includes('示例数据')`), 'Sample-data fallback is never waived')
    if (!(await evaluate(`document.body.innerText.includes('真实数据')`))) {
      if (process.env.ISSUE_WAIVE_LIVE_BADGE === '1')
        throw Object.assign(new Error('Explicit waiver: missing real-data label only; actual live metadata verified'), {
          badgeWaived: true,
        })
      assert.fail('Live badge missing (set ISSUE_WAIVE_LIVE_BADGE=1 only with explicit approval for this label)')
    }
    return 'Rendered live badge; does not prove mock process was stopped'
  })
  await test('UI-MEMORY-FILTER', 'required', 'immutable-frontend-requirement', async () => {
    await navigate('/cn/memoryList')
    assert.equal(
      await evaluate(`document.querySelectorAll('input[type="date"],input[type="datetime-local"],button[aria-label="筛选"]').length`),
      0,
    )
    blocked(
      'Upstream 481a291 has search/type tabs but no time/subject filter controls; the old TODO button was removed, not implemented. API filtering is not UI completion.',
    )
  })
  await test('UI-USAGE-CONTROLS', 'required', 'immutable-frontend-requirement', async () => {
    await navigate('/cn/usage')
    assert.equal(await evaluate(`document.querySelectorAll('input').length`), 0)
    blocked(
      'Upstream 481a291 adds 7/14/30-day ranges, but still has no usage pagination/search controls. Backend cannot supply missing page interactions.',
    )
  })
  await test('UI-MEMORY-DETAIL', 'required', 'browser-real-readonly', async () => {
    await navigate('/cn/memoryList')
    const begin = Date.now()
    await click(`document.querySelector('tbody button[aria-label="查看详情"]')`)
    await waitFor(() => evaluate(`!!document.querySelector('[role="dialog"] dd')`), 'real memory detail rendered')
    const responses = await apiResponsesSince<Memory>(begin, (r) => r.path.endsWith('/memory/detail'))
    assert.equal(responses.length, 1, 'Detail must issue a single ID lookup')
    const detail = responses[0].data
    assert.ok(detail.memoryId && detail.memoryContent, 'Nonempty real detail required')
    const rendered = await evaluate(`document.querySelector('[role="dialog"] dd').textContent`)
    assert.equal(rendered, detail.memoryContent, 'Displayed full text differs from detail API')
    await click(`document.querySelector('[role="dialog"] button[aria-label="Close"]')`)
    await waitFor(() => evaluate(`!document.querySelector('[role="dialog"]')`), 'detail closed')
    return { singleIdRequest: true, fullContentMatches: true, writes: 0 }
  })
  await test('UI-USAGE-RANGES', 'required', 'browser-real-readonly', async () => {
    const initial = await navigate('/cn/usage')
    const first = await apiResponsesSince<UsageResult>(initial, (r) => r.path.endsWith('/usage/query'))
    const cache = new Map([[14, first.at(-1)!.data.dailyStats]])
    const selected = []
    for (const n of [7, 30, 14]) {
      const begin = Date.now()
      await click(`[...document.querySelectorAll('button')].find(e=>e.textContent.trim()===${JSON.stringify(`近 ${n} 天`)})`)
      await waitFor(
        () =>
          evaluate(
            `document.querySelectorAll('tbody tr').length===${n} && [...document.querySelectorAll('button')].some(e=>e.textContent.trim()===${JSON.stringify(`近 ${n} 天`)} && e.getAttribute('aria-pressed')==='true')`,
          ),
        `range ${n} selected and rendered`,
      )
      const issued = [...requests.values()].some((r) => r.started >= begin && r.path.endsWith('/usage/query'))
      if (issued) {
        const responses = await apiResponsesSince<UsageResult>(begin, (r) => r.path.endsWith('/usage/query'))
        assert.ok(responses.every((r) => r.data.dailyStats.length === n))
        cache.set(n, responses.at(-1)!.data.dailyStats)
      }
      // TanStack Query legitimately reuses the initial 14-day response within staleTime.
      // A network request per click is not a requirement; actual rendered values still are.
      const source = cache.get(n)
      assert.ok(source, 'Range has neither a real response nor previously verified cached data')
      const actual = await evaluate(
        `[...document.querySelectorAll('tbody tr')].map(tr=>[...tr.querySelectorAll('td')].slice(0,4).map(td=>td.textContent.trim()))`,
      )
      assert.deepEqual(
        actual,
        [...source]
          .reverse()
          .map((d) => [
            d.date,
            (d.addMessage?.calls ?? 0).toLocaleString('en-US'),
            (d.searchMemory?.calls ?? 0).toLocaleString('en-US'),
            ((d.addMessage?.calls ?? 0) + (d.searchMemory?.calls ?? 0)).toLocaleString('en-US'),
          ]),
      )
      selected.push({ days: n, source: issued ? 'fresh API response' : 'previously verified real response cache' })
    }
    return { ranges: selected, realDataMatchesRenderedTable: true }
  })
  await test('UI-USAGE-TOTALS', 'required', 'browser-real-reconciliation', async () => {
    const begin = await navigate('/cn/usage')
    const replies = await apiResponsesSince<UsageResult>(begin, (r) => r.path.endsWith('/usage/query'))
    const days = replies.at(-1)!.data.dailyStats
    assert.ok(days.length && days.every((d) => Number.isInteger(d.totalCalls)), 'Updated backend totals missing')
    const total = days.reduce((n, d) => n + d.totalCalls, 0)
    const subset = days.reduce((n, d) => n + (d.addMessage?.calls ?? 0) + (d.searchMemory?.calls ?? 0), 0)
    const rendered = await evaluate(
      `(() => {const label=[...document.querySelectorAll('div')].find(e=>e.children.length===0&&e.textContent==='调用次数');return label?.nextElementSibling?.textContent??null})()`,
    )
    assert.ok(rendered !== null && /^[\d,]+$/.test(rendered as string), 'Rendered usage KPI not found')
    const shown = Number((rendered as string).replaceAll(',', ''))
    assert.equal(shown, subset, 'UI does not match its declared add/search calculation')
    if (shown !== total)
      blocked(
        `Rendered usage=${shown}, deployed all-event total=${total}; immutable frontend still reads add/search only. Backend correctness is not full UI acceptance.`,
      )
    return { rendered: shown, allEventTotal: total, scope: 'current dataset only' }
  })
  await test('UI-TEMPLATE-DETAIL', 'bonus', 'immutable-frontend-requirement', async () => {
    await navigate('/cn/profileTemplate')
    assert.ok(await evaluate(`document.body.innerText.includes('功能建设中')`))
    blocked('Upstream 481a291 replaced template list/detail with ComingSoon; existing backend CRUD cannot supply the removed UI.')
  })
  for (const label of tabs)
    await test(`UI-TAB-${label}`, 'required', 'browser-real', async () => {
      const type = ['text_mem', 'pref_mem', 'event_mem', 'profile_mem', 'skill_mem', 'tool_mem'][tabs.indexOf(label)]
      await navigate('/cn/memoryList')
      const start = Date.now()
      // The first tab is selected on initial render; reload is its real request.
      if (label === tabs[0]) {
        const begin = await navigate('/cn/memoryList')
        await apiResponsesSince(begin, (r) => r.memoryType === type)
      } else {
        await click(`[...document.querySelectorAll('[role="tab"]')].find(e => e.innerText.startsWith(${JSON.stringify(label)}))`)
        await waitFor(
          () => [...requests.values()].some((r) => r.started >= start && r.path.endsWith('/memory/list') && r.done),
          'tab API response',
        )
        await apiResponsesSince(start, (r) => r.memoryType === type)
      }
      await waitFor(() => evaluate(`!(${tableLoading})`), 'tab settled')
      assert.ok(await evaluate(`!(${tableError})`), 'Tab failed')
      return 'Native tab click + successful API response; mapping correctness belongs to fixture-backed tests'
    })
  await test('UI-EMPTY', 'required', 'browser-real', async () => {
    await navigate('/cn/memoryList')
    await click(`document.querySelector('input[placeholder="请输入记忆搜索内容"]')`)
    const start = Date.now()
    await send('Input.insertText', { text: `acceptance-no-match-${randomUUID()}` })
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 })
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 })
    await waitFor(() => evaluate(tableEmpty), 'empty state')
    const replies = await apiResponsesSince(start)
    assert.ok(
      replies.some((r) => r.path.endsWith('/memory/list') && (r.data as MemoryPage).total === 0),
      'Empty UI does not match API total',
    )
    return 'Unique nonmatching search shows 暂无数据 and total=0'
  })
  await test('UI-MEMORY-BYTES', 'required', 'browser-real', async () => {
    const start = await navigate('/cn/memoryList')
    const replies = await apiResponsesSince(
      start,
      (r) => r.path.endsWith('/memory/list') && r.memoryType === 'text_mem' && r.pageSize === 10,
    )
    assert.ok(replies.length)
    assert.ok(
      replies.every((r) => r.bytes! < 200000),
      'Memory response exceeds 200000 bytes',
    )
    return {
      maxDecodedBytes: Math.max(...replies.map((r) => r.bytes!)),
      scope: 'current real dataset; not proof of worst-case 10 large records',
    }
  })
  await test('UI-LOADING', 'required', 'browser-simulated-network', async () => {
    intercept = 'hold'
    await send('Fetch.enable', { patterns: [{ urlPattern: '*api/dashboard/project/memory/list*', requestStage: 'Request' }] })
    try {
      await send('Page.navigate', { url: `${base}/cn/memoryList` })
      await waitFor(() => evaluate(tableLoading), 'visible loading skeleton')
    } finally {
      intercept = null
      for (const id of paused) await send('Fetch.continueRequest', { requestId: id }).catch(() => {})
      paused.clear()
      await send('Fetch.disable')
    }
    await waitFor(() => evaluate(`!(${tableLoading})`), 'loading cleared')
    return 'Browser-only delayed response; no service pause'
  })
  await test('UI-ERROR', 'required', 'browser-simulated-network', async () => {
    intercept = 'fail'
    await send('Fetch.enable', { patterns: [{ urlPattern: '*api/dashboard/project/memory/list*', requestStage: 'Request' }] })
    try {
      await send('Page.navigate', { url: `${base}/cn/memoryList` })
      await waitFor(
        () => evaluate(`(${tableError}) && document.querySelector('#root')?.innerText.includes('记忆列表')`),
        'rendered error without blank page',
      )
    } finally {
      intercept = null
      await send('Fetch.disable')
    }
    return 'Injected 502; actual upstream-stop acceptance remains a separate case'
  })
  await test('UI-LOG-10000', 'required', 'browser-isolated-synthetic', async () => {
    if (process.env.ISSUE_LOG_FIXTURE !== '10000') return performanceCase(output)
    const start = await navigate('/cn/requests')
    await waitFor(
      () =>
        evaluate(
          `document.querySelectorAll('tbody tr').length === 10 && !(${tableLoading}) && document.querySelector('tbody')?.innerText.includes('searchMemory')`,
        ),
      '10000-log first page visible',
    )
    const domMs = Date.now() - start
    const replies = await apiResponsesSince<LogPage>(start, (r) => r.path.endsWith('/log/list'))
    assert.equal(replies.length, 1)
    assert.equal(replies[0].data.total, 10000)
    assert.equal(replies[0].data.logs.length, 10)
    const apiMs = replies[0].finished! - replies[0].started
    assert.ok(apiMs < 1000, `Cold API ${apiMs}ms exceeds 1s`)
    assert.ok(domMs < 1000, `Navigation to visible DOM ${domMs}ms exceeds 1s`)
    let missingFieldsDetail = false
    if (process.env.ISSUE_LOG_MISSING_FIELDS === '1') {
      const detailStart = Date.now()
      await click(`document.querySelector('tbody tr td')`)
      await waitFor(() => evaluate(`!!document.querySelector('[role="dialog"] pre')`), 'missing-field log detail rendered')
      const details = await apiResponsesSince<LogDetail>(detailStart, (r) => r.path.endsWith('/log/detail'))
      for (const field of ['requestBody', 'responseBody'] as const) {
        assert.equal(typeof details.at(-1)!.data[field], 'string')
        const value = JSON.parse(details.at(-1)!.data[field]!)
        assert.ok(
          value === null || (typeof value === 'object' && Object.keys(value).length === 0),
          'Missing fields must not invent log contents',
        )
      }
      missingFieldsDetail = true
    }
    return { rows: 10000, visibleRows: 10, apiMs, domMs, cold: true, missingFieldsDetail }
  })
  if (process.env.ISSUE_WORKSPACE_MANIFEST) {
    await workspaceCases(
      { base, apiBase, test, navigate, click, evaluate, waitFor, apiResponsesSince, requests, send, output },
      process.env.ISSUE_WORKSPACE_MANIFEST,
    )
  }
  await test('UI-LOG-CORRELATION', 'required', 'browser-real', async () => {
    if (!process.env.ISSUE_AUDIT_ORACLE) blocked('Prepare a fresh authenticated MCP call and native JSONL oracle first')
    return auditCase({ navigate, waitFor, evaluate, click, apiResponsesSince }, process.env.ISSUE_AUDIT_ORACLE)
  })
  await test('UI-STOP-MOCK', 'required', 'browser-real-isolated-lifecycle', async () => {
    if (process.env.ISSUE_MOCK_FIXTURE !== '1') return mockIndependence(output)
    const rawResponse = await fetch(`${process.env.ISSUE_UPSTREAM_BASE}/product/get_memory`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_id: 'dashboard-demo',
        mem_cube_id: 'dashboard-demo',
        include_preference: false,
        include_tool_memory: false,
        include_skill_memory: false,
        page: null,
        page_size: null,
      }),
      signal: AbortSignal.timeout(10000),
    })
    assert.equal(rawResponse.status, 200)
    const raw = (await rawResponse.json()) as Envelope<NativeBuckets>
    assert.equal(raw.code, 200)
    const expected = raw.data.text_mem
      .filter((g) => g.cube_id === 'dashboard-demo')
      .flatMap((g) => g.memories)
      .filter((r) => (r.metadata?.status || 'activated') === 'activated')
      .map((r) => r.id)
      .sort()
    assert.ok(expected.length > 0 && expected.length <= 10, 'Use bounded nonempty real data')
    async function checkLive() {
      const start = await navigate('/cn/memoryList')
      const replies = await apiResponsesSince<MemoryPage>(start, (r) => r.memoryType === 'text_mem' && r.pageSize === 10)
      const data = replies.at(-1)!.data
      assert.deepEqual(data.memListItemResponseList.map((r) => r.memoryId).sort(), expected)
      await waitFor(
        () =>
          evaluate(
            `${JSON.stringify(data.memListItemResponseList.map((r) => r.memoryContent))}.every(s=>document.querySelector('tbody')?.textContent.includes(s))`,
          ),
        'real memory rows visible',
      )
      assert.ok(await evaluate(`!document.body.innerText.includes('示例数据') && !(${tableError})`))
    }
    assert.equal((await (await fetch(base + '/api/meta')).json()).data.mode, 'sample')
    await checkLive()
    const stopped = await fetch(process.env.ISSUE_CONTROL_URL + '/stop', {
      method: 'POST',
      headers: { 'X-Test-Control': process.env.ISSUE_CONTROL_TOKEN! },
      signal: AbortSignal.timeout(10000),
    })
    assert.equal(stopped.status, 200)
    assert.equal((await stopped.json()).stopped, true)
    await assert.rejects(fetch(process.env.ISSUE_MOCK_ORIGIN + '/api/meta', { signal: AbortSignal.timeout(2000) }))
    assert.ok((await fetch(base + '/api/meta')).status >= 500, 'Configured mock proxy must actually be unavailable')
    await checkLive()
    return {
      mockProcessStopped: true,
      configuredDefaultProxyUnavailable: true,
      nativeIdsMatchedBeforeAndAfter: true,
      realRecords: expected.length,
      mode: 'live',
      memoryWrites: 0,
    }
  })
  await test('UI-STOP-UPSTREAM', 'required', 'browser-real-lifecycle', async () => {
    if (
      !process.env.ISSUE_MEMOS_CONTAINER ||
      process.env.ISSUE_ALLOW_STOP_UPSTREAM !== process.env.ISSUE_MEMOS_CONTAINER ||
      !process.argv.includes('--allow-service-stop')
    )
      blocked('Explicit task-container stop authorization is required')
    await send('Fetch.disable')
    const before = await navigate('/cn/memoryList')
    const initial = (await apiResponsesSince<MemoryPage>(before, (r) => r.memoryType === 'text_mem' && r.pageSize === 10)).at(-1)!.data
    assert.ok(initial.total > 0)
    await withUpstreamStopped(async () => {
      const start = await navigate('/cn/memoryList', { waitForIdle: false })
      try {
        await waitFor(
          () =>
            [...requests.values()].some(
              (r) =>
                r.started >= start &&
                r.hasProjectId &&
                r.memoryType === 'text_mem' &&
                r.pageSize === 10 &&
                (r.failed || (r.done && r.status! >= 500)),
            ),
          'valid scoped request fails against stopped service',
          30000,
        )
        await waitFor(() => evaluate(tableError), 'real stopped-service error visible', 30000)
      } catch (error) {
        const dom = await evaluate(
          `({loading:(${tableLoading}),error:(${tableError}),rows:document.querySelectorAll('tbody tr').length,nav:document.querySelectorAll('a[href^="/cn/"]').length,visibility:document.visibilityState})`,
        )
        const network = [...requests.values()]
          .filter((r) => r.started >= start && r.path.startsWith('/api/'))
          .map((r) => ({ path: r.path, status: r.status, done: r.done, failed: r.failed, type: r.memoryType, pageSize: r.pageSize }))
        throw new Error(`${(error as Error).message}; ${JSON.stringify({ dom, network })}`, { cause: error })
      }
      const failures = [...requests.values()].filter(
        (r) => r.started >= start && r.hasProjectId && r.memoryType === 'text_mem' && r.pageSize === 10,
      )
      assert.ok(
        failures.length > 0 && failures.every((r) => r.failed || r.status! >= 500),
        'Must observe real HTTP/network failure, not fallback data',
      )
      assert.ok(
        await evaluate(
          `document.querySelectorAll('a[href^="/cn/"]').length >= 5 && document.body.innerText.includes('记忆列表') && !document.body.innerText.includes('示例数据')`,
        ),
      )
    })
    const restored = await navigate('/cn/memoryList')
    const current = (await apiResponsesSince<MemoryPage>(restored, (r) => r.memoryType === 'text_mem' && r.pageSize === 10)).at(-1)!.data
    assert.equal(current.total, initial.total)
    assert.deepEqual(
      current.memListItemResponseList.map((r) => r.memoryId).sort(),
      initial.memListItemResponseList.map((r) => r.memoryId).sort(),
    )
    await waitFor(() => evaluate(`!(${tableError}) && document.querySelectorAll('tbody tr').length > 0`), 'restored real data visible')
    return {
      actualTaskContainerStopped: true,
      fetchInterceptionDisabled: true,
      clearErrorWithoutBlankPageOrSampleFallback: true,
      serviceRestoredHealthy: true,
      originalIdsUnchanged: true,
    }
  })
  for (const [id, reason] of [
    ['UI-DELETE-REAL', 'Requires a run-owned memory and explicit mutation consent; execute detailed case in docs/ISSUE-ACCEPTANCE.md'],
    ['UI-PROJECT-SWITCH', 'Requires two dedicated workspaces and restoring global selected state'],
    ['UI-LOG-CORRELATION', 'Requires a fresh authenticated MCP call and its raw JSONL record'],
    ['UI-LOG-10000', 'Requires an isolated log dataset with >=10000 rows and cold BFF start'],
    ['UI-STOP-UPSTREAM', 'Requires an isolated controllable service; injected browser errors are insufficient'],
    ['UI-STOP-MOCK', 'Requires verified isolated mock lifecycle; never stop the existing demo automatically'],
  ])
    if (!rows.some((row) => row.id === id)) rows.push({ id, requirement: 'required', scope: 'browser-real', status: 'BLOCKED', reason })
} catch (error) {
  rows.push({
    id: 'UI-ENVIRONMENT',
    requirement: 'required',
    scope: 'browser-real',
    status: (error as ToolError).blocked ? 'BLOCKED' : 'FAIL',
    reason: (error as Error).message,
  })
} finally {
  for (const item of planned)
    if (!rows.some((row) => row.id === item.id))
      rows.push({
        ...item,
        scope: 'browser-not-run',
        status: 'BLOCKED',
        reason: 'Browser setup or execution did not reach this planned case',
      })
  // Close only the browser launched with this disposable profile, including launcher handoffs.
  if (socket?.readyState === WebSocket.OPEN) await send('Browser.close').catch(() => {})
  socket?.close()
  for (const item of pending.values()) {
    clearTimeout(item.timer)
    item.fail(new Error('Browser runner closing'))
  }
  if (browser && browser.exitCode === null) {
    browser.kill()
    await Promise.race([new Promise((ok) => browser!.once('exit', ok)), delay(5000)])
  }
  if (profile) await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }).catch(() => {})
  await mkdir(output, { recursive: true })
  const overallStatus = outcome(rows)
  const selectedStatus = selectedCases ? outcome(rows, selectedCases) : overallStatus
  await writeFile(
    join(output, 'browser.json'),
    JSON.stringify(
      {
        schemaVersion: 1,
        generatedAt: new Date().toISOString(),
        scope: 'browser; incomplete live acceptance remains blocked',
        overallStatus,
        selectedStatus,
        selectedCases: selectedCases ? [...selectedCases] : null,
        bootstrapRejections: [...requests.values()].filter((r) => r.bootstrapRejection).map((r) => r.bootstrapRejection),
        rows,
      },
      null,
      2,
    ),
  )
  for (const row of rows)
    if (!selectedCases || selectedCases.has(row.id) || row.id === 'UI-ENVIRONMENT')
      console.log(`${row.status} ${row.id}${row.reason ? `: ${row.reason}` : ''}`)
  console.log(`Selected execution: ${selectedStatus}; overall planned acceptance: ${overallStatus}`)
  process.exitCode = selectedStatus === 'PASS' ? 0 : selectedStatus === 'BLOCKED' ? 2 : 1
}
