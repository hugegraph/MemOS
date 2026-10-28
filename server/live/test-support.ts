import type { ApiKey, Memory, Project, ProfileTemplate, Records, Meta } from '../../shared/contract.ts'
import type { DashboardInput, NativeClient, RouteContext, Routes } from './types.ts'

export interface Envelope<T> {
  code: number
  data: T
  message?: string
}
export interface HealthMeta extends Meta {
  health: { status?: string; code?: number }
}
export interface MemoryPage {
  total: number
  current: number
  size: number
  memListItemResponseList: Memory[]
}
export type RouteResult<P extends string> = P extends `${string}/project/dict`
  ? Project[]
  : P extends `${string}/project/create` | `${string}/project/update`
    ? Project
    : P extends `${string}/memory/list`
      ? MemoryPage
      : P extends `${string}/memory/detail`
        ? Memory
        : P extends `${string}/keys/create`
          ? ApiKey
          : P extends `${string}/keys/list`
            ? Records<ApiKey>
            : P extends `${string}/keys/copy`
              ? string
              : P extends `${string}/profileTemplate/create` | `${string}/profileTemplate/detail`
                ? ProfileTemplate
                : P extends `${string}/profileTemplate/list`
                  ? { total: number; list: ProfileTemplate[] }
                  : unknown

/** Complete mock port: unexpected calls fail instead of silently succeeding. */
export function nativeStub(overrides: Partial<NativeClient> = {}): NativeClient {
  const unexpected = async (): Promise<never> => {
    throw new Error('Unexpected native client call')
  }
  return { health: unexpected, request: unexpected, createCube: unexpected, getMemory: unexpected, deleteMemory: unexpected, ...overrides }
}
/** Exercise the same handler context the HTTP dispatcher supplies. */
export async function invoke<P extends string>(routes: Routes, path: P, context: Partial<RouteContext> = {}): Promise<RouteResult<P>> {
  return (await routes[path]({ body: {}, query: new URLSearchParams(), params: [], ...context })) as RouteResult<P>
}
export type RecordedBody = DashboardInput & { user_id?: string; mem_cube_id?: string; page?: null }
