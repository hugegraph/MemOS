import type { DashboardInput } from '../../../server/live/types.ts'
import type { Envelope, Check } from '../../tool-types.ts'
export interface ClickPoint {
  x: number
  y: number
  hit: boolean
  visibility?: string
  focused?: boolean
  targetTag?: string
  hitTag?: string
  role?: string | null
}
export type Evaluate = (expression: string) => Promise<unknown>
export type WaitFor = (fn: () => unknown | Promise<unknown>, description: string, timeoutMs?: number) => Promise<void>
export type TestDriver = (id: string, requirement: string, scope: string, operation: () => Promise<unknown>) => Promise<void>
export interface NativeInputContext {
  send(method: string, params?: Record<string, unknown>): Promise<unknown>
  evaluate: Evaluate
  waitFor: WaitFor
  observe?: (point: ClickPoint | null) => void
}
export interface BootstrapRequest {
  path: string
  method: string
  loaderId?: string
  started: number
  status?: number
  body?: Record<string, unknown>
  envelope?: { code: number }
  [key: string]: unknown
}
export interface DictionaryEvidence {
  loaderId?: string
  started: number
  finished?: number
  done?: boolean
  status?: number
  envelope?: { code: number }
  data: { id: string; status?: string }[]
}
export interface NetworkRecord<T = unknown> extends BootstrapRequest {
  id: string
  origin: string
  url?: string
  done?: boolean
  failed?: boolean
  networkError?: string
  finished?: number
  hasProjectId?: boolean
  memoryType?: string
  projectId?: string
  pageSize?: number
  deleteIds?: string[]
  requestFields?: Record<string, string>
  body?: DashboardInput
  bytes?: number
  data?: T
  envelope?: Envelope<T>
  bootstrapRejection?: unknown
}
export type ApiResponse<T = unknown> = NetworkRecord<T> & { data: T; envelope: Envelope<T> }
export type ApiResponses = <T = unknown>(time: number, filter?: (r: NetworkRecord) => boolean) => Promise<ApiResponse<T>[]>
export interface CdpResponses {
  'Network.getResponseBody': { body: string; base64Encoded: boolean }
  'Page.navigate': { loaderId?: string; errorText?: string }
  'Runtime.evaluate': { result: { value: unknown }; exceptionDetails?: unknown }
  'Page.captureScreenshot': { data: string }
}
export type CdpResult<M extends string> = M extends keyof CdpResponses ? CdpResponses[M] : Record<string, unknown>
export type Send = <M extends string>(method: M, params?: Record<string, unknown>) => Promise<CdpResult<M>>
export interface BrowserContext {
  apiBase: string
  output: string
  test: TestDriver
  navigate(path: string, options?: { waitForIdle?: boolean }): Promise<number>
  click(expression: string): Promise<void>
  evaluate: Evaluate
  waitFor: WaitFor
  send: Send
  apiResponsesSince: ApiResponses
  requests: Map<string, NetworkRecord>
  blocked(reason: string): never
}
export interface BrowserReport {
  status?: string
  overallStatus: string
  rows: (Check & { requirement?: string })[]
  [key: string]: unknown
}
