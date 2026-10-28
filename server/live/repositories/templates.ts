import { atomicJson, readJson } from './atomic-json.ts'
import type { StoredTemplate } from '../types.ts'
import { ApiError } from '../errors.ts'

function validate(value: unknown): StoredTemplate[] {
  if (!Array.isArray(value)) throw new ApiError('模板存储必须为数组', 50300, 503)
  const seen = new Set<string>()
  for (const row of value) {
    if (
      !row ||
      typeof row !== 'object' ||
      Array.isArray(row) ||
      !['templateId', 'projectId', 'templateName'].every((k) => typeof row[k] === 'string' && row[k].trim()) ||
      seen.has(row.templateId)
    )
      throw new ApiError('模板存储身份无效或重复', 50300, 503)
    seen.add(row.templateId)
    if (row.bindingCount !== undefined && (!Number.isSafeInteger(row.bindingCount) || row.bindingCount < 0))
      throw new ApiError('模板绑定计数无效', 50300, 503)
    if (row.updatedAt !== undefined && (typeof row.updatedAt !== 'string' || !Number.isFinite(Date.parse(row.updatedAt))))
      throw new ApiError('模板更新时间无效', 50300, 503)
    for (const [key, group] of Object.entries(row)) {
      if (['templateId', 'projectId', 'templateName', 'updatedAt', 'bindingCount'].includes(key)) continue
      if (!group || typeof group !== 'object' || Array.isArray(group)) throw new ApiError('模板分组结构无效', 50300, 503)
      for (const field of Object.values(group))
        if (
          !field ||
          typeof field !== 'object' ||
          Array.isArray(field) ||
          (field.algorithm_updatable !== undefined && typeof field.algorithm_updatable !== 'boolean') ||
          (field.value !== undefined && typeof field.value !== 'string')
        )
          throw new ApiError('模板字段结构无效', 50300, 503)
    }
  }
  return structuredClone(value)
}

export class TemplateRepository {
  declare file: string
  #rows: StoredTemplate[]
  declare write: typeof atomicJson
  constructor(file: string, write = atomicJson) {
    this.file = file
    this.write = write
    this.#rows = validate(readJson(file, []))
  }
  all() {
    return structuredClone(this.#rows)
  }
  replace(rows: unknown) {
    const next = validate(rows)
    if (JSON.stringify(readJson(this.file, [])) !== JSON.stringify(this.#rows))
      throw new ApiError('模板存储已被其他实例修改，请重新加载后重试', 40900, 409)
    // Do not publish mutated in-memory state on failed disk writes.
    this.write(this.file, next)
    this.#rows = next
  }
}
