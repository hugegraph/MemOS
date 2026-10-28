import { writeFileSync, renameSync, unlinkSync, existsSync, readFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'

/** One writer per directory. Readers never see a partially written JSON document. */
export function atomicJson(file: string, value: unknown) {
  const temp = `${file}.${randomUUID()}.tmp`
  try {
    writeFileSync(temp, JSON.stringify(value, null, 2), { mode: 0o600, flag: 'wx', flush: true })
    renameSync(temp, file)
  } finally {
    if (existsSync(temp)) unlinkSync(temp)
  }
}

export function readJson(file: string, fallback: unknown): unknown {
  return existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) : fallback
}
