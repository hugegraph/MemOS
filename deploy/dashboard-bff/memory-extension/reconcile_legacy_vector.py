"""Soft-mark only manifest-owned legacy orphan vectors; never delete/recreate nodes.

This is test cleanup, NOT a replacement for the authenticated production soft-delete API.
"""
import hashlib
import json
import os
import re
import sys
from neo4j import GraphDatabase
from qdrant_client import QdrantClient, models

p = json.load(sys.stdin)
assert re.fullmatch(r'batch3-[0-9a-f]{8}', p['run'])
assert re.fullmatch(r'proj_[0-9a-f-]{36}', p['cube'])
assert 1 <= len(p['records']) <= 4
digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()
driver = GraphDatabase.driver(os.environ['NEO4J_URI'], auth=(os.environ['NEO4J_USER'], os.environ['NEO4J_PASSWORD']))
kwargs = {'api_key': os.getenv('QDRANT_API_KEY')}
if os.getenv('QDRANT_URL'): kwargs['url'] = os.environ['QDRANT_URL']
else: kwargs.update(host=os.environ.get('QDRANT_HOST', 'qdrant'), port=int(os.environ.get('QDRANT_PORT', '6333')))
client = QdrantClient(**kwargs)
results = []
try:
    for record in p['records']:
        with driver.session(database=os.environ.get('NEO4J_DB_NAME', 'neo4j')) as session:
            assert session.run('MATCH (n:Memory {id:$id}) RETURN count(n) AS count', id=record['id']).single()['count'] == 0
        points = client.retrieve(collection_name='neo4j_vec_db', ids=[record['id']], with_payload=True, with_vectors=True)
        if not points:
            results.append({'id': record['id'], 'historicalNodeMissing': True, 'vectorAlreadyAbsent': True})
            continue
        assert len(points) == 1
        before = points[0]
        assert before.payload['user_name'] == p['cube'] and before.payload['user_id'] == p['user']
        assert p['run'] in before.payload['memory']
        assert hashlib.sha256(before.payload['memory'].encode()).hexdigest() == record['memoryHash']
        assert before.payload['status'] in ('activated', 'deleted')
        selector = models.Filter(must=[models.HasIdCondition(has_id=[record['id']]),
            models.FieldCondition(key='user_name', match=models.MatchValue(value=p['cube'])),
            models.FieldCondition(key='user_id', match=models.MatchValue(value=p['user'])),
            models.FieldCondition(key='memory', match=models.MatchValue(value=before.payload['memory']))])
        client.set_payload(collection_name='neo4j_vec_db', payload={'status': 'deleted'}, points=selector, wait=True)
        after = client.retrieve(collection_name='neo4j_vec_db', ids=[record['id']], with_payload=True, with_vectors=True)[0]
        assert after.payload['status'] == 'deleted'
        assert digest(after.vector) == digest(before.vector)
        assert digest({k:v for k,v in after.payload.items() if k != 'status'}) == digest({k:v for k,v in before.payload.items() if k != 'status'})
        results.append({'id': record['id'], 'historicalNodeMissing': True, 'vectorRetained': True, 'vectorStatus': 'deleted', 'vectorAndOtherPayloadUnchanged': True})
    print(json.dumps({'status': 'PASS', 'kind': 'owned-legacy-orphan-cleanup-only', 'records': results, 'note': 'Does not turn the historical physical node deletion into a soft-delete PASS'}))
finally:
    driver.close()
    client.close()
