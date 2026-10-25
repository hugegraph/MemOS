// Explicit deployment-specific, read-only storage oracle. Queries one manifest-owned UUID only.
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { createHash } from 'node:crypto'
import { remoteProfile } from '../remote-profile.ts'
import type { OwnedProject, OwnedMemory } from '../../tool-types.ts'
export interface StorageSnapshot {
  status: string
  id: string
  storedStatus: string
  vectorStatus: string
  graphFingerprint: string
  vectorFingerprint: string
  payloadFingerprint: string
  edges: number
  contentAndCubeMatch: boolean
}

export async function storageSnapshot(project: OwnedProject, record: OwnedMemory): Promise<StorageSnapshot> {
  assert.equal(process.env.ISSUE_NATIVE_DELETE_ORACLE, 'ssh-docker', 'Select the authorized SSH/Docker read-only oracle explicitly')
  const profile = remoteProfile()
  assert.match(project.id, /^proj_[0-9a-f-]{36}$/)
  assert.match(record.id, /^[0-9a-f-]{36}$/)
  assert.ok(project.memories.some((r) => r.id === record.id && r.memory === record.memory))
  const run = /^(batch3-[0-9a-f]{8})-(?:A|B|empty)$/.exec(project.name)
  assert.ok(run && record.memory.includes(run[1]))
  const input = Buffer.from(
    JSON.stringify({ id: record.id, cube: project.id, hash: createHash('sha256').update(record.memory).digest('hex') }),
  ).toString('base64')
  const script = `set -e
${profile.setup}
docker exec -i ${profile.memos} python - <<'PY'
import os,json,base64,hashlib,urllib.request
from neo4j import GraphDatabase
p=json.loads(base64.b64decode('${input}'))
with GraphDatabase.driver(os.environ['NEO4J_URI'],auth=(os.environ['NEO4J_USER'],os.environ['NEO4J_PASSWORD'])) as driver:
 with driver.session(database=os.environ.get('NEO4J_DB_NAME','neo4j')) as session:
  rows=list(session.run('MATCH (n:Memory {id: $id, user_name: $cube}) OPTIONAL MATCH (n)-[r]-() RETURN n AS node, count(r) AS edges', id=p['id'], cube=p['cube']))
  assert len(rows)==1, 'Expected one owned memory node'
  node=dict(rows[0]['node'])
  assert hashlib.sha256(node['memory'].encode()).hexdigest()==p['hash'], 'Content fingerprint mismatch'
  assert node['user_name']==p['cube'], 'Cube mismatch'
  headers={'Content-Type':'application/json'}
  if os.getenv('QDRANT_API_KEY'): headers['api-key']=os.environ['QDRANT_API_KEY']
  url='http://'+os.environ.get('QDRANT_HOST','qdrant')+':'+os.environ.get('QDRANT_PORT','6333')+'/collections/neo4j_vec_db/points'
  req=urllib.request.Request(url,data=json.dumps({'ids':[p['id']],'with_payload':True,'with_vector':True}).encode(),headers=headers)
  with urllib.request.urlopen(req,timeout=5) as response: points=json.load(response)['result']
  assert len(points)==1 and str(points[0]['id'])==p['id'], 'Owned vector must be retained'
  point=points[0]
  assert point['payload']['user_name']==p['cube'], 'Vector cube mismatch'
  digest=lambda x:hashlib.sha256(json.dumps(x,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
  print(json.dumps({'status':'PASS','id':p['id'],'storedStatus':node['status'],'vectorStatus':point['payload']['status'],
   'graphFingerprint':digest({k:v for k,v in node.items() if k not in ('status','delete_record_id','delete_time')}),
   'vectorFingerprint':digest(point['vector']), 'payloadFingerprint':digest({k:v for k,v in point['payload'].items() if k!='status'}),
   'edges':rows[0]['edges'],'contentAndCubeMatch':True}))
PY
`
  const encoded = Buffer.from(script).toString('base64')
  const { stdout } = await promisify(execFile)(
    'ssh',
    ['-T', '-o', 'RemoteCommand=none', '-o', 'BatchMode=yes', profile.target, `echo ${encoded} | base64 -d | bash`],
    { timeout: 20000, maxBuffer: 4000 },
  )
  const result = JSON.parse(stdout.trim())
  assert.equal(result.status, 'PASS')
  assert.equal(result.id, record.id)
  return result
}

export async function deletedStatus(project: OwnedProject, record: OwnedMemory) {
  const result = await storageSnapshot(project, record)
  assert.equal(result.storedStatus, 'deleted')
  assert.equal(result.vectorStatus, 'deleted')
  return result
}
