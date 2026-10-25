import type { Project, ProfileTemplate, RequestLog, LogDetail, UsageDailyStat } from '../../shared/contract.ts'
import type { ValidateFunction } from 'ajv'

export interface Settings {
  upstream: string
  timeout: number
  port: number
  host: string
  service: string
  origins: Set<string>
  dataDir: string
  auditDir: string
  projects: unknown
  owner: string
  gatewayEnv: string
  promptToken: string
  memoryToken: string
}
export interface Principal {
  userId: string
  cubeId: string
}
export interface WorkspaceBinding {
  projectId: string
  principal: Readonly<Principal>
  auditTenant: string
}
export interface WorkspaceRecord extends Project {
  userId: string
  cubeId: string
  tenant?: string
}
export interface WorkspaceReader {
  get(id: unknown): WorkspaceRecord
  all(): WorkspaceRecord[]
  resolveWorkspace(id: unknown): WorkspaceBinding
}
export interface WorkspaceStorage {
  all(): WorkspaceRecord[]
  get(id: unknown): WorkspaceRecord
  replace(records: unknown): void
}
export interface Pagination {
  pageNum?: number
  pageSize?: number
}
export type Page<T, K extends string = 'records'> = Record<K, T[]> & { total: number; current: number; size: number }
/** Known Dashboard input fields. Unknown extension fields are preserved, not reinterpreted. */
export interface DashboardInput extends Pagination {
  [key: string]: unknown
  id?: string
  name?: string
  projectId?: string
  description?: string
  status?: string
  isMultiView?: number
  memoryId?: string
  memory_ids?: string[]
  memType?: string
  memoryDetailType?: string
  knowledgeBaseId?: string
  startTime?: string
  endTime?: string
  startDate?: string
  endDate?: string
  externalUserId?: string
  subjectType?: string
  subjectId?: string
  memSearchContent?: string
  eventType?: string
  query?: string
  logId?: string
  templateId?: string
  templateName?: string
  keyName?: string
  apiKeyId?: string
  prompt?: string
  extractionTypes?: string[]
  messages?: ChatMessage[]
}
export interface ChatMessage {
  role: string
  content: string
  [key: string]: unknown
}
export interface RouteContext {
  body: DashboardInput
  query: URLSearchParams
  params: string[]
}
export type RouteHandler = (context: RouteContext) => unknown | Promise<unknown>
export type Routes = Record<string, RouteHandler>
export interface Operation {
  path: string
  method: string
  pattern: RegExp
  validate: ValidateFunction | null
}
export interface OpenApiOperation {
  requestBody?: { content?: Record<string, { schema?: Record<string, unknown> }> }
}
export interface OpenApiSpec {
  paths: Record<string, Record<string, OpenApiOperation>>
  components: { schemas: { EventType: { enum: string[] } }; [key: string]: unknown }
}
export interface NativeEnvelope {
  code?: number
  data?: unknown
  [key: string]: unknown
}
export interface RequestOptions {
  headers?: Record<string, string>
  timeout?: number
}
export interface TransportOptions {
  method: string
  headers: Record<string, string>
  body: string | undefined
  signal: AbortSignal
  redirect: 'error'
}
export type Transport = (url: string, options: TransportOptions) => Promise<Response>
export interface NativeRequester {
  request(path: string, body?: unknown, options?: RequestOptions): Promise<NativeEnvelope>
}
export interface CubeCreator {
  createCube(body: { cube_name: string; cube_id: string; owner_id: string }): Promise<NativeEnvelope>
}
export interface NativeMemoryClient {
  getMemory(binding: WorkspaceBinding, bucket: string): Promise<NativeEnvelope>
  deleteMemory(ids: string[], binding: WorkspaceBinding): Promise<unknown>
}
export interface NativeClient extends NativeRequester, CubeCreator, NativeMemoryClient {
  health(): Promise<NativeEnvelope>
}
export interface MemoryMetadata {
  [key: string]: unknown
  type?: string | null
  dashboard_type?: string | null
  status?: string
  created_at?: string
  subject_type?: string
  user_id?: unknown
  user_name?: unknown
}
export interface NativeMemory {
  id: string
  memory?: unknown
  metadata?: MemoryMetadata | null
}
export interface NativeMemoryGroup {
  cube_id?: string
  memories?: NativeMemory[]
}
export interface RawAudit extends Record<string, unknown> {
  ts?: unknown
  tenant?: unknown
  tool?: string
  input?: unknown
  output?: unknown
}
export interface AuditRecord {
  id: string
  raw: RawAudit
  time: number
  eventType: string
  tenant: string
  cubeId?: string
  apiKeyId?: string
  traceId: string
  userId: string
  agentId: string
  duration: number
  status: string
}
export interface AuditSource {
  name: string
  offset: number
  line: string | Uint8Array
}
export interface AuditScope {
  mode: string
  unattributedRecords: number
  unattributedKeyRecords?: number
}
export interface AttributedLog extends RequestLog {
  cubeId: string | null
  attribution: 'cube' | 'unknown'
}
export interface AttributedLogDetail extends LogDetail {
  cubeId: string | null
  attribution: 'cube' | 'unknown'
}
export interface AuditStorage {
  rows(): Promise<AuditRecord[]>
  badLines(): number
}
export interface AuditWorkspaces {
  get(id: unknown): Pick<WorkspaceRecord, 'cubeId' | 'tenant'>
  all(): Pick<WorkspaceRecord, 'cubeId' | 'tenant'>[]
}
export interface AuditReader extends AuditStorage {
  scoped(rows: AuditRecord[], projectId?: string): AuditRecord[]
  scopeMetadata(rows: AuditRecord[], projectId?: string): AuditScope
}
export interface AuditFile {
  offset: number
  rows: AuditRecord[]
  bad: number
  ino?: number
  size?: number
  mtime?: number
  prefixHash?: string
}
export interface DailyUsage extends UsageDailyStat {
  totalCalls: number
  callsByEvent: Record<string, number>
  addMessage: { calls: number; totalTokens: number }
  searchMemory: { calls: number; totalTokens: number }
  rerank?: { calls: number; totalTokens: number }
}
export type TenantEntry = string | { user_id: string; cube_id?: string }
export type TenantEntries = Record<string, TenantEntry>
export interface GatewaySnapshot {
  text: string
  entries: TenantEntries
  tenants: Record<string, Principal>
  suffix: string
}
export interface GatewayConfig {
  load(): GatewaySnapshot
  save(snapshot: GatewaySnapshot, entries: TenantEntries): void
}
export type KeyMetadata = Record<string, { name: string; createTime: string }>
export interface StoredTemplate extends ProfileTemplate {
  projectId?: string
}
export interface PromptData {
  prompt?: string
  updatedAt?: string
  ok?: boolean
  results?: { type: string; content: string }[]
  extractionTypes?: string[]
}
export interface ScopedPromptClient {
  execute(action: string, binding: Pick<WorkspaceBinding, 'principal'>, payload: DashboardInput): Promise<PromptData>
}
