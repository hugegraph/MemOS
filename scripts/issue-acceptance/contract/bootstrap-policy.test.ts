import test from 'node:test'
import assert from 'node:assert/strict'
import { verifyBootstrapRejection } from '../browser/bootstrap-policy.ts'
import type { BootstrapRequest, DictionaryEvidence } from '../browser/types.ts'
import type { Envelope } from '../../tool-types.ts'

function fixture() {
  const request: BootstrapRequest = {
    path: '/api/dashboard/project/memory/list',
    method: 'POST',
    loaderId: 'doc1',
    started: 1,
    status: 400,
    body: { memoryDetailType: 'text_mem', pageNum: 1, pageSize: 10 },
  }
  const envelope: Envelope<unknown> = { code: 40000, data: null }
  const dictionary: DictionaryEvidence = {
    loaderId: 'doc1',
    started: 2,
    finished: 3,
    done: true,
    status: 200,
    envelope: { code: 0 },
    data: [{ id: 'A', status: 'ENABLED' }],
  }
  const successful: BootstrapRequest[] = [
    { ...request, status: 200, started: 4, body: { ...request.body, projectId: 'A' }, envelope: { code: 0 } },
  ]
  return { request, envelope, dictionary, successful }
}
test('bootstrap: initial invalid request is a verified rejection, with subsequent valid data required', () => {
  const f = fixture()
  assert.equal(verifyBootstrapRejection(f.request, f.envelope, f.dictionary, f.successful).noData, true)
})
for (const [name, mutate] of [
  [
    'server failure',
    (f) => {
      f.request.status = 502
      f.envelope.code = 50200
    },
  ],
  [
    'wrong scope',
    (f) => {
      f.request.body!.projectId = 'B'
    },
  ],
  [
    'late request',
    (f) => {
      f.request.started = 9
    },
  ],
  [
    'other document',
    (f) => {
      f.request.loaderId = 'doc2'
    },
  ],
  [
    'failed dictionary business status',
    (f) => {
      f.dictionary.envelope!.code = 50000
    },
  ],
  [
    'wrong fields',
    (f) => {
      f.request.body!.pageSize = '10'
    },
  ],
  [
    'leaked data',
    (f) => {
      f.envelope.data = { records: ['secret'] }
    },
  ],
  [
    'no eventual success',
    (f) => {
      f.successful = []
    },
  ],
  [
    'success in other scope',
    (f) => {
      f.successful[0].body!.projectId = 'B'
    },
  ],
  [
    'write operation',
    (f) => {
      f.request.path = '/api/dashboard/project/memory/delete'
    },
  ],
] satisfies [string, (f: ReturnType<typeof fixture>) => void][])
  test(`bootstrap must not hide ${name}`, () => {
    const f = fixture()
    mutate(f)
    assert.throws(() => verifyBootstrapRejection(f.request, f.envelope, f.dictionary, f.successful))
  })
