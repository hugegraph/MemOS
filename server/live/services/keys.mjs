import { existsSync, readFileSync, writeFileSync, renameSync, unlinkSync } from 'node:fs'
import { join } from 'node:path'
import { randomBytes, randomUUID, createHash } from 'node:crypto'
import { ApiError } from '../errors.mjs'
import { paginate } from '../query.mjs'

const keyId = (token) => createHash('sha256').update(token).digest('hex').slice(0, 24)
const matches = (tenant, binding) => tenant?.userId === binding.principal.userId && tenant?.cubeId === binding.principal.cubeId

export class KeyService {
  constructor(workspaces, adapter, dataDir) {
    this.workspaces = workspaces
    this.adapter = adapter
    this.file = join(dataDir, 'key-metadata.json')
    this.metadata = existsSync(this.file) ? JSON.parse(readFileSync(this.file, 'utf8')) : {}
  }
  saveMetadata(next) {
    const temporary = `${this.file}.${randomUUID()}.tmp`
    try {
      writeFileSync(temporary, JSON.stringify(next), { mode: 0o600, flag: 'wx' })
      renameSync(temporary, this.file)
      this.metadata = next
    } finally {
      if (existsSync(temporary)) unlinkSync(temporary)
    }
  }
  publicKey(token) {
    return {
      id: keyId(token),
      apiKey: token,
      keyName: this.metadata[keyId(token)]?.name || '已配置密钥',
      createTime: this.metadata[keyId(token)]?.createTime || '',
    }
  }
  visible(snapshot) {
    const bindings = this.workspaces.all().map((project) => this.workspaces.resolveWorkspace(project.id))
    return Object.keys(snapshot.tenants).filter((token) => bindings.some((binding) => matches(snapshot.tenants[token], binding)))
  }
  list(query) {
    const binding = this.workspaces.resolveWorkspace(query.projectId)
    const snapshot = this.adapter.load()
    return paginate(
      Object.keys(snapshot.tenants)
        .filter((token) => matches(snapshot.tenants[token], binding))
        .map((token) => this.publicKey(token)),
      query,
    )
  }
  create(body) {
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
  lookup(id, snapshot) {
    const token = this.visible(snapshot).find((value) => keyId(value) === id)
    if (!token) throw new ApiError('密钥不存在', 40001, 404)
    return token
  }
  copy(id) {
    return this.lookup(id, this.adapter.load())
  }
  revoke(id) {
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
