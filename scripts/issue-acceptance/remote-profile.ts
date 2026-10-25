// Explicit SSH/Docker test target. No workstation names, credentials or service defaults.
import assert from 'node:assert/strict'

export function remoteProfile(env: NodeJS.ProcessEnv = process.env, { lifecycle = false, audit = false } = {}) {
  const required = (key: string, pattern: RegExp) => {
    const value = env[key]
    assert.ok(typeof value === 'string' && pattern.test(value), `Configure ${key} explicitly with a safe value`)
    return value
  }
  const name = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/
  const target = required('ISSUE_SSH_TARGET', /^[A-Za-z0-9_][A-Za-z0-9_.@-]*$/)
  const memos = required('ISSUE_MEMOS_CONTAINER', name)
  const dockerHost = env.ISSUE_DOCKER_HOST
  if (dockerHost) assert.match(dockerHost, /^unix:\/\/[A-Za-z0-9_./-]+$/, 'Only an explicit local Unix Docker socket is supported')
  return {
    target,
    memos,
    gateway: audit ? required('ISSUE_GATEWAY_CONTAINER', name) : undefined,
    bff: audit ? required('ISSUE_BFF_CONTAINER', name) : undefined,
    project: lifecycle ? required('ISSUE_COMPOSE_PROJECT', name) : undefined,
    service: lifecycle ? required('ISSUE_MEMOS_SERVICE', name) : undefined,
    imageId: lifecycle ? required('ISSUE_MEMOS_IMAGE_ID', /^sha256:[0-9a-f]{64}$/) : undefined,
    setup: `export PATH="$HOME/bin:$PATH"\n${dockerHost ? `export DOCKER_HOST='${dockerHost}'\n` : ''}`,
  }
}
