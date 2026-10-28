import test from 'node:test'
import assert from 'node:assert/strict'
import Ajv from 'ajv/dist/2020.js'
import { spec } from '../isolated/fixture.ts'

function validate(name: string, data: unknown) {
  const ajv = new Ajv({ strict: false })
  const schema = spec.components.responses[name].content['application/json'].schema
  return ajv.compile({ ...schema, components: spec.components })({ code: 0, data, message: 'ok' })
}

test('KB placeholder write accepts null or complete object, not incomplete objects/scalars', () => {
  assert.equal(validate('KnowledgeBase', null), true)
  assert.equal(validate('KnowledgeBase', { kbId: 'real-id', knowledgebaseName: 'Real KB' }), true)
  for (const value of [{}, { kbId: 'missing-name' }, 0, 'ok', []]) assert.equal(validate('KnowledgeBase', value), false)
})

test('KB placeholder detail accepts only empty object or complete object, not arbitrary partial data', () => {
  assert.equal(validate('KnowledgeBaseDetail', {}), true)
  assert.equal(validate('KnowledgeBaseDetail', { kbId: 'real-id', knowledgebaseName: 'Real KB' }), true)
  for (const value of [null, { kbId: 'missing-name' }, 0, []]) assert.equal(validate('KnowledgeBaseDetail', value), false)
})

test('KB association nullable response does not weaken shared Ok or KB list item schemas', () => {
  assert.equal(validate('KnowledgeBaseAssociation', null), true)
  assert.equal(validate('KnowledgeBaseAssociation', {}), true)
  assert.equal(validate('KnowledgeBaseAssociation', 'ok'), false)
  assert.equal(validate('Ok', null), false)
  assert.equal(validate('KnowledgeBaseRecords', { records: [{}], total: 1 }), false)
})
