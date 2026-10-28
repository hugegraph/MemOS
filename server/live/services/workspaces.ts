import { randomUUID } from 'node:crypto'
import { unlinkSync, existsSync } from 'node:fs'
import { atomicJson, readJson } from '../repositories/atomic-json.ts'
import { ApiError } from '../errors.ts'
import { workspaceBinding, toDashboardProject } from '../domain/workspace.ts'
import { paginate } from '../query.ts'
import type { WorkspaceStorage, CubeCreator, DashboardInput, WorkspaceRecord } from '../types.ts'
type PendingCreation = { project: WorkspaceRecord; phase: 'planned' | 'upstream-confirmed' }

export class WorkspaceService {
  #creating = false
  #pendingName: string | undefined
  declare repository: WorkspaceStorage
  declare client: CubeCreator
  declare owner: string
  declare recoveryFile?: string
  #pending: PendingCreation | undefined
  constructor(repository: WorkspaceStorage, client: CubeCreator, owner = '', recoveryFile?: string) {
    this.repository = repository
    this.client = client
    this.owner = owner
    this.recoveryFile = recoveryFile
    if (recoveryFile) {
      const pending = readJson(recoveryFile, null) as PendingCreation | null
      if (pending) {
        workspaceBinding(pending.project)
        if (!['planned', 'upstream-confirmed'].includes(pending.phase)) throw new ApiError('工作区恢复记录无效', 50300, 503)
        this.#pending = pending
      }
    }
  }
  savePending(pending: PendingCreation) {
    if (this.recoveryFile) atomicJson(this.recoveryFile, pending)
    this.#pending = pending
  }
  get pendingRecovery() {
    return this.#pending
  }
  clearPending() {
    if (this.recoveryFile && existsSync(this.recoveryFile)) unlinkSync(this.recoveryFile)
    this.#pending = undefined
  }
  get(id: unknown) {
    return this.repository.get(id)
  }
  all() {
    return this.repository.all()
  }
  resolveWorkspace(id: unknown) {
    return workspaceBinding(this.get(id))
  }
  dict() {
    return this.all().map(toDashboardProject)
  }
  list(query: DashboardInput) {
    return paginate(this.dict(), query)
  }
  validateName(name: unknown, exceptId?: string) {
    if (exceptId !== undefined && typeof name === 'string' && name.trim() === this.#pendingName)
      throw new ApiError('该名称正用于创建工作区，请稍后重试', 40900, 409)
    if (typeof name !== 'string' || !name.trim() || this.all().some((p) => p.id !== exceptId && p.name === name.trim()))
      throw new ApiError('项目名称为空或已存在')
    return name.trim()
  }
  async create(body: DashboardInput) {
    if (this.#pending) {
      const { project, phase } = this.#pending
      if (phase !== 'upstream-confirmed' || body.name?.trim() !== project.name)
        throw new ApiError(`工作区创建待核对：${project.id}（${phase}）；禁止盲目重复创建`, 40900, 409)
      if (!this.all().some((p) => p.id === project.id)) {
        this.validateName(project.name)
        const existing = this.all()
        this.repository.replace([...existing, { ...project, status: existing.length ? 'DISABLED' : 'ENABLED' }])
      }
      this.clearPending()
      return toDashboardProject(this.get(project.id))
    }
    const name = this.validateName(body.name)
    if (!this.owner) throw new ApiError('新建上游工作区需配置 MEMOS_CUBE_OWNER（已存在的上游用户）', 50100, 501)
    if (this.#creating) throw new ApiError('已有工作区创建请求，请稍后重试', 40900, 409)
    this.#creating = true
    this.#pendingName = name
    try {
      const id = `proj_${randomUUID()}`
      const project: WorkspaceRecord = {
        id,
        name,
        description: body.description || '',
        isMultiView: body.isMultiView || 0,
        status: this.all().length ? 'DISABLED' : 'ENABLED',
        createTime: new Date().toISOString(),
        cubeId: id,
        userId: this.owner,
        tenant: this.owner,
      }
      // Write-ahead identity survives a crash or ambiguous timeout after native creation.
      this.savePending({ project, phase: 'planned' })
      const result = await this.client.createCube({ cube_name: name, cube_id: id, owner_id: this.owner })
      if ((result?.data as { cube_id?: unknown } | null | undefined)?.cube_id !== id)
        throw new ApiError('上游创建工作区响应的 cube_id 不匹配', 50200, 502)
      this.savePending({ project, phase: 'upstream-confirmed' })
      // A concurrent update may have used this name while create_cube was awaited.
      this.validateName(name)
      const projects = this.all()
      this.repository.replace([...projects, project])
      this.clearPending()
      return toDashboardProject(project)
    } finally {
      this.#creating = false
      this.#pendingName = undefined
    }
  }
  update(body: DashboardInput) {
    this.get(body.id)
    if (body.status !== undefined && !['ENABLED', 'DISABLED'].includes(body.status)) throw new ApiError('无效的工作区选择状态')
    const projects = this.all(),
      project = projects.find((p) => p.id === body.id)!
    if (body.name !== undefined) project.name = this.validateName(body.name, body.id)
    if (body.description !== undefined) project.description = body.description
    if (body.isMultiView !== undefined) project.isMultiView = body.isMultiView
    if (body.status === 'ENABLED') for (const other of projects) other.status = 'DISABLED'
    if (body.status !== undefined) project.status = body.status
    if (!projects.some((p) => p.status === 'ENABLED')) {
      const next = body.status === 'DISABLED' ? projects.find((p) => p.id !== project.id) : projects[0]
      if (!next) throw new ApiError('不能取消最后一个工作区的选择，请使用撤销接口移除工作区')
      next.status = 'ENABLED'
    }
    this.repository.replace(projects)
    return toDashboardProject(project)
  }
  revoke(id: string) {
    this.get(id)
    const remaining = this.all().filter((p) => p.id !== id)
    if (remaining.length && !remaining.some((p) => p.status === 'ENABLED')) remaining[0].status = 'ENABLED'
    this.repository.replace(remaining)
    return { ok: true }
  }
}
