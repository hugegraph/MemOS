// An immutable UI can send an invalid request before its project dictionary arrives.
// Verify that rejection as a negative case; never count it as successful data access.
import assert from 'node:assert/strict'
import { operations, validateInput } from '../../../server/live/core.ts'
import type { BootstrapRequest, DictionaryEvidence } from './types.ts'
import type { Envelope } from '../../tool-types.ts'

const reads = new Set(['project/memory/list', 'log/list', 'keys/list', 'profileTemplate/list'].map((p) => '/api/dashboard/' + p))

export function isBootstrapCandidate(request: BootstrapRequest) {
  return reads.has(request.path) && request.method === 'POST' && !request.body?.projectId
}

export function verifyBootstrapRejection(
  request: BootstrapRequest,
  envelope: Envelope<unknown>,
  dictionary: DictionaryEvidence,
  successful: BootstrapRequest[],
) {
  assert.ok(reads.has(request.path), 'Only known initial read operations may reject missing scope')
  assert.equal(request.method, 'POST')
  assert.ok(request.loaderId && request.loaderId === dictionary.loaderId, 'Dictionary must belong to same document')
  assert.ok(dictionary.done && dictionary.status === 200 && request.started <= dictionary.finished!, 'Not a pre-readiness request')
  assert.equal(dictionary.envelope?.code, 0, 'Dictionary business status must succeed')
  assert.ok(!Object.hasOwn(request.body!, 'projectId') || request.body!.projectId === '', 'A supplied scope must never be ignored')
  const selected = dictionary.data.find((p) => p.status === 'ENABLED')
  assert.ok(selected?.id, 'A real selected workspace must subsequently be resolved')
  const route = operations.find((op) => op.path === request.path && op.method === 'POST')
  validateInput(route!, { ...request.body, projectId: selected.id })
  assert.ok([400, 404].includes(request.status!), 'Only explicit validation/not-found rejection is expected')
  assert.ok([40000, 40001].includes(envelope.code), 'Do not hide upstream/server failures')
  assert.equal(envelope.data, null, 'No workspace data may leak on rejected request')
  assert.ok(
    successful.some(
      (r) =>
        r.loaderId === request.loaderId &&
        r.path === request.path &&
        r.body?.projectId === selected.id &&
        r.status === 200 &&
        r.envelope?.code === 0 &&
        r.started >= dictionary.started,
    ),
    'The same operation must subsequently succeed with the selected scope',
  )
  return { path: request.path, status: request.status, code: envelope.code, noData: true, scopedSuccessRequired: true }
}
