import { existsSync, readFileSync, writeFileSync, renameSync, unlinkSync } from 'node:fs'
import { join } from 'node:path'
import { randomBytes, randomUUID, createHash } from 'node:crypto'
import { ApiError } from '../errors.ts'
import { paginate } from '../query.ts'
import type { ApiKey } from '../../../shared/contract.ts'
import type { WorkspaceReader, GatewayConfig, KeyMetadata, Principal, WorkspaceBinding, GatewaySnapshot, DashboardInput } from '../types.ts'

const keyId = (token: string) => createHash('sha256').update(token).digest('hex').slice(0, 24)
const matches = (tenant: Principal | undefined, binding: WorkspaceBinding) =>
  tenant?.userId === binding.principal.userId && tenant?.cubeId === binding.principal.cubeId

export class KeyService {
  declare workspaces: Pick<WorkspaceReader, 'all' | 'resolveWorkspace'>
  declare adapter: GatewayConfig
  declare file: string
  declare metadata: KeyMetadata
  constructor(workspaces: Pick<WorkspaceReader, 'all' | 'resolveWorkspace'>, adapter: GatewayConfig, dataDir: string) {
    this.workspaces = workspaces
    this.adapter = adapter
    this.file = join(dataDir, 'key-metadata.json')
    this.metadata = existsSync(this.file) ? JSON.parse(readFileSync(this.file, 'utf8')) : {}
  }
  saveMetadata(next: KeyMetadata) {
    const temporary = `${this.file}.${randomUUID()}.tmp`
    try {
      writeFileSync(temporary, JSON.stringify(next), { mode: 0o600, flag: 'wx' })
      renameSync(temporary, this.file)
      this.metadata = next
    } finally {
      if (existsSync(temporary)) unlinkSync(temporary)
    }
  }
  publicKey(token: string): ApiKey {
    return {
      id: keyId(token),
      apiKey: token,
      keyName: this.metadata[keyId(token)]?.name || '已配置密钥',
      createTime: this.metadata[keyId(token)]?.createTime || '',
    }
  }
  visible(snapshot: GatewaySnapshot) {
    const bindings = this.workspaces.all().map((project) => this.workspaces.resolveWorkspace(project.id))
    return Object.keys(snapshot.tenants).filter((token) => bindings.some((binding) => matches(snapshot.tenants[token], binding)))
  }
  list(query: DashboardInput) {
    const binding = this.workspaces.resolveWorkspace(query.projectId)
    const snapshot = this.adapter.load()
    return paginate(
      Object.keys(snapshot.tenants)
        .filter((token) => matches(snapshot.tenants[token], binding))
        .map((token) => this.publicKey(token)),
      query,
    )
  }
  create(body: DashboardInput) {
    const binding = this.workspaces.resolveWorkspace(body.projectId)
    if (typeof body.keyName !== 'string' || !body.keyName.trim()) throw new ApiError('密钥名称不能为空')
    const snapshot = this.adapter.load()
    const token = `sk-${randomBytes(24).toString('hex')}`
    const { userId, cubeId } = binding.principal
    // Preserve compact legacy format when identities coincide; otherwise retain the actual cube.
    const tenant = userId === cubeId ? userId : { user_id: userId, cube_id: cubeId }
    this.adapter.save(snapshot, { ...snapshot.entries, [token]: tenant })
    this.saveMetadata({ ...this.metadata, [keyId(token)]: { name: body.keyName.trim(), createTime: new Date().toISOString() } })
    return this.publicKey(token)
  }
  lookup(id: unknown, snapshot: GatewaySnapshot) {
    const token = this.visible(snapshot).find((value) => keyId(value) === id)
    if (!token) throw new ApiError('密钥不存在', 40001, 404)
    return token
  }
  copy(id: unknown) {
    return this.lookup(id, this.adapter.load())
  }
  revoke(id: string) {
    const snapshot = this.adapter.load(),
      token = this.lookup(id, snapshot)
    const entries = { ...snapshot.entries },
      metadata = { ...this.metadata }
    delete entries[token]
    delete metadata[id]
    this.adapter.save(snapshot, entries)
    this.saveMetadata(metadata)
    return { ok: true }
  }
}
