import test from 'node:test'
import assert from 'node:assert/strict'
import { writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { fixture, spec, P } from '../isolated/fixture.mjs'
// OpenAPI has no operationId: METHOD + template path is the stable inventory ID.
export const operationInventory = Object.entries(spec.paths).flatMap(([path, methods]) =>
  Object.keys(methods).map((method) => ({
    id: `${method.toUpperCase()} ${path}`,
    path,
    method: method.toUpperCase(),
    placeholder: path.includes('/knowledgebase/') || path.includes('/skill/') || path.includes('/api/user/'),
  })),
)
test('I1-CONTRACT-INVENTORY: 37 operations; 11 permitted placeholders are NOT feature acceptance', () => {
  assert.equal(operationInventory.length, 37)
  assert.equal(operationInventory.filter((x) => x.placeholder).length, 11)
})
for (const op of operationInventory)
  test(
    `I1-CONTRACT ${op.id}${op.placeholder ? ' [placeholder schema ONLY; not feature acceptance]' : ''}`,
    { timeout: 20000 },
    async (t) => {
      const h = await fixture(t, 1)
      let path = op.path
      let body = {
        projectId: 'A',
        name: 'contract',
        description: 'fixture',
        keyName: 'contract',
        memoryDetailType: 'text_mem',
        memType: 'text_mem',
        memoryId: 'A-0',
        memory_ids: ['A-0'],
        knowledgebaseName: 'contract',
        knowledgeBaseId: 'kb',
        knowledgeBaseIds: ['kb'],
        id: 'A',
        templateName: 'contract',
        prompt: 'extract explicit facts',
        messages: [{ role: 'user', content: 'I like tea' }],
        oss_url: 'https://invalid.example/test',
      }
      if (path.includes('/keys/copy') || path.includes('/keys/revoke/')) {
        const k = await h.request(P + 'keys/create', { projectId: 'A', keyName: 'contract' })
        body.apiKeyId = k.id
        path = path.replace('{apiKeyId}', k.id)
      }
      if (path.includes('/profileTemplate/') && !path.endsWith('/create') && !path.endsWith('/list')) {
        const v = await h.request(P + 'profileTemplate/create', { projectId: 'A', templateName: 'contract' })
        body = { projectId: 'A', templateId: v.templateId, templateName: 'updated' }
        if (op.method === 'GET') path += `?projectId=A&templateId=${v.templateId}`
      }
      if (path.endsWith('/project/revoke/{id}')) path = path.replace('{id}', 'empty')
      if (path.endsWith('/log/detail')) {
        await writeFile(
          join(h.dir, 'calls-contract.jsonl'),
          JSON.stringify({ ts: '2026-10-01T12:00:00Z', tenant: 'A', cube_id: 'A', tool: 'add_message', ok: true }) + '\n',
        )
        body.logId = (await h.request(P + 'log/list', { projectId: 'A' })).logs[0].id
      }
      if (path.endsWith('/project/memory/list')) body = { projectId: 'A', memoryDetailType: 'text_mem' }
      // Strip extraneous keys for schemas with typed additionalProperties.
      if (path.endsWith('/profileTemplate/create')) body = { projectId: 'A', templateName: 'contract' }
      const result = await h.request(path, body, op.method)
      // User-approved Issue #1 placeholder contract: exact values, not arbitrary empty success.
      if (/\/knowledgebase\/(create|update|project\/associate|project\/remove)$/.test(path)) assert.equal(result, null)
      if (path.endsWith('/knowledgebase/detail')) assert.deepEqual(result, {})
    },
  )
