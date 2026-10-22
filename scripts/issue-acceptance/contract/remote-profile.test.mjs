import test from 'node:test'
import assert from 'node:assert/strict'
import { remoteProfile } from '../remote-profile.mjs'

test('remote test profile has no implicit machine/service target', () => {
  assert.throws(() => remoteProfile({}), /ISSUE_SSH_TARGET/)
  assert.throws(() => remoteProfile({ ISSUE_SSH_TARGET: 'test-host' }), /ISSUE_MEMOS_CONTAINER/)
})
test('remote test profile rejects SSH options and shell interpolation', () => {
  const base = { ISSUE_SSH_TARGET: 'tester@host', ISSUE_MEMOS_CONTAINER: 'acceptance-memos' }
  for (const target of ['-oProxyCommand=anything', 'host;true', '$(id)', 'host name'])
    assert.throws(() => remoteProfile({ ...base, ISSUE_SSH_TARGET: target }))
  assert.throws(() => remoteProfile({ ...base, ISSUE_MEMOS_CONTAINER: 'memos;true' }))
  assert.throws(() => remoteProfile({ ...base, ISSUE_DOCKER_HOST: 'unix:///tmp/socket;true' }))
  assert.throws(() => remoteProfile(base, { lifecycle: true }), /ISSUE_COMPOSE_PROJECT/)
  assert.throws(() => remoteProfile(base, { audit: true }), /ISSUE_GATEWAY_CONTAINER/)
})
test('lifecycle requires exact owner labels and immutable image identity', () => {
  const env = {
    ISSUE_SSH_TARGET: 'host',
    ISSUE_MEMOS_CONTAINER: 'memos',
    ISSUE_DOCKER_HOST: 'unix:///run/user/1000/docker.sock',
    ISSUE_COMPOSE_PROJECT: 'acceptance',
    ISSUE_MEMOS_SERVICE: 'memos',
    ISSUE_MEMOS_IMAGE_ID: 'sha256:' + 'a'.repeat(64),
  }
  const profile = remoteProfile(env, { lifecycle: true })
  assert.equal(profile.project, 'acceptance')
  assert.equal(profile.memos, 'memos')
  assert.ok(profile.setup.includes(env.ISSUE_DOCKER_HOST))
  assert.throws(() => remoteProfile({ ...env, ISSUE_MEMOS_IMAGE_ID: 'image:latest' }, { lifecycle: true }))
})
