import type {
  ApiKey,
  Memory,
  Project,
  ProfileTemplate,
  Records,
  RequestLog,
  LogDetail,
  KnowledgeBase,
  Meta,
  ModelRecord,
  MemoryGraph,
} from '../shared/contract.ts'
import type { DailyUsage, AuditScope, DashboardInput, NativeMemory } from '../server/live/types.ts'
export interface Envelope<T> {
  code: number
  data: T
  message?: string
}
export type Observed<T> = T & Record<string, unknown>
export interface MemoryPage {
  memListItemResponseList: Observed<Memory>[]
  total: number
  current: number
  size: number
}
export interface UsageResult {
  dailyStats: DailyUsage[]
  auditScope: AuditScope
}
export interface LogPage {
  logs: Observed<RequestLog>[]
  total: number
  current: number
  size: number
  auditScope: AuditScope
}
export type CleanPath<P extends string> = P extends `${infer Path}?${string}` ? Path : P
export type DashboardResult<P extends string> = Result<CleanPath<P>>
type Result<P extends string> = P extends `${string}/product/get_memory`
  ? NativeBuckets
  : P extends `${string}/meta`
    ? Meta
    : P extends `${string}project/dict`
      ? Project[]
      : P extends `${string}project/create` | `${string}project/update`
        ? Project
        : P extends `${string}project/list`
          ? Records<Project>
          : P extends `${string}project/memory/list`
            ? MemoryPage
            : P extends `${string}project/memory/detail`
              ? Observed<Memory>
              : P extends `${string}project/memory/delete`
                ? { deleted: number }
                : P extends `${string}keys/create`
                  ? ApiKey
                  : P extends `${string}keys/list`
                    ? Records<ApiKey>
                    : P extends `${string}keys/copy`
                      ? string
                      : P extends `${string}log/list`
                        ? LogPage
                        : P extends `${string}log/detail`
                          ? Observed<LogDetail>
                          : P extends `${string}usage/query`
                            ? UsageResult
                            : P extends
                                  `${string}profileTemplate/create` | `${string}profileTemplate/update` | `${string}profileTemplate/detail`
                              ? ProfileTemplate
                              : P extends `${string}profileTemplate/list`
                                ? { list: ProfileTemplate[]; total: number; current: number; size: number; pages: number }
                                : P extends
                                      `${string}knowledgebase/create` | `${string}knowledgebase/detail` | `${string}knowledgebase/update`
                                  ? KnowledgeBase
                                  : P extends `${string}knowledgebase/list`
                                    ? Records<KnowledgeBase>
                                    : P extends `${string}knowledgebase/file/list` | `${string}knowledgebase/memory/list`
                                      ? Records<Record<string, unknown>>
                                      : P extends `${string}quota/summary`
                                        ? Record<string, number>
                                        : P extends `${string}subscription/details`
                                          ? { cycles: { id: string }[]; currentCycle: { id: string } }
                                          : P extends `${string}models`
                                            ? ModelRecord[]
                                            : P extends `${string}graph`
                                              ? MemoryGraph
                                              : P extends `${string}prompt-config/detail`
                                                ? { projectId: string; prompt: string; updatedAt: string }
                                                : P extends `${string}prompt-config/generate` | `${string}prompt-config/reset-default`
                                                  ? { prompt: string }
                                                  : P extends `${string}prompt-config/debug/extract`
                                                    ? { results: { type: string; content: string }[]; extractionTypes: string[] }
                                                    : unknown
export interface Schema {
  [key: string]: unknown
  $ref?: string
  type?: string
  properties?: Record<string, Schema>
  required?: string[]
  items?: Schema
  enum?: string[]
  additionalProperties?: boolean | Schema
}
export interface ResponseSpec {
  $ref?: string
  content: Record<string, { schema: Schema }>
}
export interface OperationSpec {
  requestBody?: { content: Record<string, { schema: Schema }> }
  responses?: Record<string, ResponseSpec>
}
export interface ToolSpec {
  paths: Record<string, Record<string, OperationSpec>>
  components: { schemas: Record<string, Schema>; responses: Record<string, ResponseSpec>; [key: string]: unknown }
}
export interface ToolError extends Error {
  code?: string | number
  blocked?: boolean
  badgeWaived?: boolean
  stdout?: string
  stderr?: string
}
export interface Check {
  id?: string
  name?: string
  status?: string
  required?: boolean
  reason?: string
  evidence?: unknown
  [key: string]: unknown
}
export interface Report {
  kind?: string
  status: string
  checks?: Check[]
  phases?: Check[]
  failure?: unknown
  reason?: string
  [key: string]: unknown
}
export interface CheckedReport extends Report {
  checks: Check[]
}
export interface OwnedMemory {
  id: string
  memory: string
  type?: string | null
  [key: string]: unknown
}
export interface OwnedProject extends Project {
  label?: string
  memories: OwnedMemory[]
}
/** Persisted acceptance manifest. Preparation builds it in stages; existing runtime guards remain authoritative. */
export interface WorkspaceManifest {
  version: number
  run: string
  bff: string
  upstream: string
  owner: string
  projects: OwnedProject[]
  state: string
  before: Project[]
  protectedId: string
  protectedUserId: string
  protectedFingerprint: string
  sdkState?: string
  sdkSeeds: OwnedMemory[]
  browserDeleted?: string[]
  legacyHardDeleted?: string[]
  [key: string]: unknown
}
export interface NativeTextMemory extends NativeMemory {
  memory: string
}
export type NativeBuckets = Record<string, { cube_id: string; memories: NativeTextMemory[] }[]>
export interface DashboardCall {
  <P extends string>(path: P, body?: unknown, expected?: 200): Promise<DashboardResult<P>>
  (path: string, body: unknown, expected: number): Promise<unknown>
}
export interface NativeAudit {
  ts: string
  tenant: string
  cube_id?: string
  tool: string
  ms?: number
  ok?: boolean
  input?: unknown
  output?: unknown
  [key: string]: unknown
}
export type RequestBody = DashboardInput & { memory_ids?: string[]; user_id?: string; cube_id?: string; mem_cube_id?: string }
