import { open, readdir, lstat } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { join } from 'node:path'
import { ApiError } from '../errors.mjs'
import { normalizeAuditRecord } from '../adapters/audit-mapper.mjs'

const digest = (buffer) => createHash('sha256').update(buffer).digest('hex')
/** Single shared incremental parser; publish a new snapshot only after every file was read successfully. */
export class AuditRepository {
  #files = new Map()
  #ordered = []
  #refreshing
  constructor(settings, { maxLineBytes = 2 * 1024 * 1024, maxFileBytes = 64 * 1024 * 1024, maxTotalBytes = 256 * 1024 * 1024 } = {}) {
    this.directory = settings.auditDir
    this.limits = { maxLineBytes, maxFileBytes, maxTotalBytes }
    if (!Object.values(this.limits).every((v) => Number.isSafeInteger(v) && v > 0)) throw new Error('Invalid audit limits')
  }
  async #refresh() {
    if (!this.directory) throw new ApiError('未配置 MEMORY_CALL_LOG_DIR', 50300, 503)
    const next = new Map()
    let total = 0
    try {
      const names = (await readdir(this.directory)).filter((name) => /^calls-.*\.jsonl$/.test(name)).sort()
      for (const name of names) {
        const path = join(this.directory, name),
          link = await lstat(path)
        if (!link.isFile() || link.isSymbolicLink()) throw new ApiError('审计路径不是普通文件', 50300, 503)
        const handle = await open(path, 'r')
        try {
          const info = await handle.stat()
          total += info.size
          if (info.size > this.limits.maxFileBytes || total > this.limits.maxTotalBytes)
            throw new ApiError('审计文件超过安全大小限制', 50300, 503)
          const old = this.#files.get(name)
          if (old && old.ino === info.ino && old.size === info.size && old.mtime === info.mtimeMs) {
            next.set(name, old)
            continue
          }
          // Bounded file snapshot. Existing prefix is hashed to detect rewrite-and-grow, not merely truncation.
          const buffer = Buffer.alloc(info.size)
          let read = 0
          while (read < buffer.length) {
            const { bytesRead } = await handle.read(buffer, read, buffer.length - read, read)
            if (!bytesRead) throw new ApiError('审计文件读取期间发生截断，请重试', 50300, 503)
            read += bytesRead
          }
          const after = await handle.stat()
          if (after.size < info.size || (after.size === info.size && after.mtimeMs !== info.mtimeMs))
            throw new ApiError('审计文件读取期间被改写，请重试', 50300, 503)
          const reusable =
            old && old.ino === info.ino && buffer.length >= old.offset && digest(buffer.subarray(0, old.offset)) === old.prefixHash
          const file = reusable ? { ...old, rows: [...old.rows] } : { offset: 0, rows: [], bad: 0 }
          for (;;) {
            const end = buffer.indexOf(10, file.offset)
            const length = (end < 0 ? buffer.length : end) - file.offset
            if (length > this.limits.maxLineBytes) throw new ApiError('审计单行超过安全大小限制', 50300, 503)
            if (end < 0) break
            const offset = file.offset,
              line = buffer.subarray(offset, end)
            file.offset = end + 1
            if (!line.toString('utf8').trim()) continue
            try {
              file.rows.push(normalizeAuditRecord(JSON.parse(line.toString('utf8')), { name, offset, line }))
            } catch {
              file.bad++
            }
          }
          Object.assign(file, { ino: info.ino, size: info.size, mtime: info.mtimeMs, prefixHash: digest(buffer.subarray(0, file.offset)) })
          next.set(name, file)
        } finally {
          await handle.close()
        }
      }
    } catch (error) {
      if (error instanceof ApiError) throw error
      throw new ApiError('审计目录或文件不可读取，请检查挂载和权限', 50300, 503)
    }
    const ordered = [...next.values()].flatMap((file) => file.rows).sort((a, b) => b.time - a.time || a.id.localeCompare(b.id))
    this.#files = next
    this.#ordered = ordered
  }
  async rows() {
    if (!this.#refreshing)
      this.#refreshing = this.#refresh().finally(() => {
        this.#refreshing = undefined
      })
    await this.#refreshing
    return this.#ordered
  }
  badLines() {
    return [...this.#files.values()].reduce((sum, file) => sum + file.bad, 0)
  }
}
