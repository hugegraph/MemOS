import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync, unlinkSync } from 'node:fs'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { ApiError } from '../errors.mjs'
import { workspaceBinding } from '../domain/workspace.mjs'

function validated(records) {
  if (!Array.isArray(records)) throw new ApiError('工作区配置必须为数组', 50300, 503)
  const seen = new Set()
  return records.map((record) => {
    const binding = workspaceBinding(record)
    if (typeof record.name !== 'string' || !record.name.trim() || seen.has(record.id))
      throw new ApiError('工作区名称无效或 ID 重复', 50300, 503)
    seen.add(record.id)
    return { ...structuredClone(record), tenant: binding.auditTenant }
  })
}

/** Keeps the existing projects.json layout; no destructive migration. One writer per data directory. */
export class WorkspaceRepository {
  #records
  constructor(settings) {
    mkdirSync(settings.dataDir, { recursive: true })
    this.file = join(settings.dataDir, 'projects.json')
    const exists = existsSync(this.file)
    this.#records = validated(exists ? JSON.parse(readFileSync(this.file, 'utf8')) : settings.projects)
    if (!exists) this.replace(this.#records)
  }
  all() {
    return structuredClone(this.#records)
  }
  get(id) {
    const record = this.#records.find((p) => p.id === id)
    if (!record) throw new ApiError('工作区不存在，请配置 DASHBOARD_WORKSPACES', 40001, 404)
    return structuredClone(record)
  }
  replace(records) {
    const next = validated(records)
    const temporary = `${this.file}.${randomUUID()}.tmp`
    try {
      writeFileSync(temporary, JSON.stringify(next, null, 2), { mode: 0o600, flag: 'wx' })
      renameSync(temporary, this.file)
      this.#records = next
    } finally {
      if (existsSync(temporary)) unlinkSync(temporary)
    }
  }
}
