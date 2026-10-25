import { existsSync, readFileSync, writeFileSync, renameSync, unlinkSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import { ApiError } from '../errors.ts'
import type { TenantEntries, Principal, GatewaySnapshot } from '../types.ts'

function entriesOf(raw?: string): TenantEntries {
  const text = (raw || '').trim()
  if (!text) return {}
  if (text.startsWith('{')) return JSON.parse(text)
  return Object.fromEntries(
    text
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
      .map((item) => {
        const separator = item.lastIndexOf(':') // gateway.domain.tenants.parse_tenants uses rpartition.
        if (separator < 1 || separator === item.length - 1) throw new Error('Invalid tenant entry')
        return [item.slice(0, separator).trim(), item.slice(separator + 1).trim()]
      }),
  )
}
function normalize(entries: TenantEntries): Record<string, Principal> {
  if (!entries || typeof entries !== 'object' || Array.isArray(entries)) throw new Error('Object required')
  return Object.fromEntries(
    Object.entries(entries).map(([token, value]) => {
      const spec = typeof value === 'string' ? { user_id: value } : value
      const userId = spec?.user_id,
        cubeId = spec?.cube_id || userId
      if (![token, userId, cubeId].every((v) => typeof v === 'string' && v.trim())) throw new Error('Invalid tenant identity')
      return [token, { userId, cubeId }]
    }),
  )
}
export function parseTenants(raw?: string) {
  try {
    return normalize(entriesOf(raw))
  } catch {
    throw new ApiError('MEMOS_TENANTS 不是有效的用户/记忆空间映射', 50300, 503)
  }
}

function dotenvValue(source: string) {
  const value = source.trim()
  const quote = value[0]
  if (quote !== "'" && quote !== '"') return { value, suffix: '' }
  const match = quote === "'" ? /^'((?:\\.|[^'\\])*)'(\s*(?:#.*)?)$/.exec(value) : /^"((?:\\.|[^"\\])*)"(\s*(?:#.*)?)$/.exec(value)
  if (!match) throw new ApiError('MEMOS_TENANTS 的环境文件引号格式无效', 50300, 503)
  const escapes: Record<string, string> = {
    '\\': '\\',
    "'": "'",
    '"': '"',
    a: '\x07',
    b: '\b',
    f: '\f',
    n: '\n',
    r: '\r',
    t: '\t',
    v: '\v',
  }
  const pattern = quote === "'" ? /\\(['\\])/g : /\\([\\'"abfnrtv])/g
  return { value: match[1].replace(pattern, (_text, code) => escapes[code]), suffix: match[2] }
}

/** The running gateway still needs a controlled restart; saving a file does not imply activation. */
export class GatewayConfigAdapter {
  declare path: string
  constructor(path: string) {
    this.path = path
  }
  load(): GatewaySnapshot {
    if (!this.path) throw new ApiError('请配置 DASHBOARD_GATEWAY_ENV 指向网关实际使用的环境文件；修改后需重启网关', 50100, 501)
    let text
    try {
      text = readFileSync(this.path, 'utf8')
    } catch {
      throw new ApiError('网关配置文件不可读取', 50300, 503)
    }
    const matches = [...text.matchAll(/^MEMOS_TENANTS=([^\r\n]*)\r?$/gm)]
    if (matches.length !== 1) throw new ApiError('网关环境文件必须恰有一条 MEMOS_TENANTS 配置', 50300, 503)
    const { value, suffix } = dotenvValue(matches[0][1])
    const tenants = parseTenants(value)
    return { text, entries: entriesOf(value), tenants, suffix }
  }
  save(snapshot: GatewaySnapshot, entries: TenantEntries) {
    const value = JSON.stringify(entries)
    parseTenants(value)
    const current = this.load()
    if (current.text !== snapshot.text) throw new ApiError('网关配置已被修改，请刷新后重试', 40900, 409)
    const encoded = value.replace(/\\/g, '\\\\').replace(/'/g, "\\'")
    const updated = current.text.replace(
      /^MEMOS_TENANTS=([^\r\n]*)(\r?)$/m,
      (_line, _value, cr) => `MEMOS_TENANTS='${encoded}'${current.suffix}${cr}`,
    )
    const temporary = `${this.path}.${randomUUID()}.tmp`
    try {
      writeFileSync(temporary, updated, { mode: 0o600, flag: 'wx' })
      renameSync(temporary, this.path)
    } finally {
      if (existsSync(temporary)) unlinkSync(temporary)
    }
  }
}
