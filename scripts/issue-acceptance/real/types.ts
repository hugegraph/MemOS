import type { Check, NativeAudit } from '../../tool-types.ts'
export interface Endpoint {
  url: string
  bearerEnv?: string
}
export interface Workspace {
  projectId: string
  cubeId: string
  userId: string
  dedicated?: boolean
}
export interface Filters {
  externalUserId?: string
  subjectType?: string
  memSearchContent?: string
  startTime?: string
  endTime?: string
}
export interface RunnerConfig {
  bff?: Endpoint
  upstream?: Endpoint
  maxCalls?: number
  timeoutMs?: number
  maxDurationMs?: number
  workspaces?: Workspace[]
  emptyWorkspace?: Workspace
  frozenFixture?: { workspace: Workspace; ids: string[]; filters: Filters[] }
  mutation?: {
    enabled: boolean
    runPrefix?: string
    workspace: Workspace
    write?: { path: string; idsPointer: string; idsField?: string; body: Record<string, unknown> }
  }
  audit?: { files: string[]; projectTenants: Record<string, string[]>; startDate: string; endDate: string }
}
export interface RunOptions {
  dryRun?: boolean
  allowMutation?: boolean
}
export interface RunReport {
  kind: string
  startedAt: string
  cases: (Check & { id: string })[]
  cleanup: Check[]
  requests: number
  status?: string
  finishedAt?: string
}
export interface AuditRow {
  raw: NativeAudit
}
