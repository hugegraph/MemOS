"""Explicit owned SDK fixtures, NOT proof of REST extraction capabilities.

Uses native validated items, embedding and add_node/add implementations. Initialization
subclasses only CHECK existing resources instead of running SDK schema initialization.
All native logs remain in a private task directory; only a sanitized report is emitted.
"""
import json
import os
import re
import sys
from pathlib import Path

p = json.load(sys.stdin)
assert re.fullmatch(r'batch3-[0-9a-f]{8}', p['run'])
assert re.fullmatch(r'proj_[0-9a-f-]{36}', p['cube'])
assert len(p['records']) == 4 and len({r['id'] for r in p['records']}) == 4
assert {r['type'] for r in p['records']} == {'pref_mem', 'tool_mem', 'skill_mem', 'event_mem'}
assert all(p['run'] in r['memory'] for r in p['records'])
report_stream = os.fdopen(os.dup(1), 'w')
directory = Path('/tmp') / ('dashboard-sdk-' + p['run'])
directory.mkdir(mode=0o700, exist_ok=True)
os.chmod(directory, 0o700)
log = os.open(directory / 'native.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
os.dup2(log, 1)
os.dup2(log, 2)
os.chdir(directory)
stage = 'native-imports'
graph = None
report = {'kind': 'owned-native-sdk-type-fixtures', 'status': 'FAIL', 'llmCalls': 0}
try:
    from memos.api.config import APIConfig
    from memos.configs.graph_db import Neo4jCommunityGraphDBConfig
    from memos.configs.embedder import EmbedderConfigFactory
    from memos.graph_dbs.neo4j import Neo4jGraphDB
    from memos.graph_dbs.neo4j_community import Neo4jCommunityGraphDB
    from memos.vec_dbs.qdrant import QdrantVecDB
    from memos.embedders.factory import EmbedderFactory
    from memos.memories.textual.item import TextualMemoryItem, TreeNodeTextualMemoryMetadata

    class ExistingQdrant(QdrantVecDB):
        def create_collection(self):
            assert self.collection_exists(self.config.collection_name), 'Existing collection required'
        def ensure_payload_indexes(self, fields):
            assert set(fields) <= set(self.client.get_collection(self.config.collection_name).payload_schema)
            self.verified_indexes = True

    class ExistingGraph(Neo4jCommunityGraphDB):
        def __init__(self, config):
            assert not config.auto_create and not config.use_multi_db
            self.vec_db = ExistingQdrant(config.vec_config.config)
            assert getattr(self.vec_db, 'verified_indexes', False)
            Neo4jGraphDB.__init__(self, config)
        def create_index(self, **kwargs):
            with self.driver.session(database=self.db_name) as session:
                names = set(session.run('SHOW INDEXES YIELD name RETURN name').value('name'))
            assert {'memory_type_index', 'memory_created_at_index', 'memory_updated_at_index', 'memory_user_name_index'} <= names

    stage = 'existing-storage-check'
    cfg = APIConfig.get_neo4j_community_config(p['user'])
    cfg['user_name'] = p['cube']
    graph = ExistingGraph(Neo4jCommunityGraphDBConfig.model_validate(cfg))
    ids = [r['id'] for r in p['records']]
    with graph.driver.session(database=graph.db_name) as session:
        existing = {r['id']: r['node'] for r in session.run('MATCH (n:Memory) WHERE n.id IN $ids RETURN n.id AS id, n AS node', ids=ids)}
    planned = {r['id']: r for r in p['records']}
    for point in graph.vec_db.client.retrieve(collection_name=graph.vec_db.config.collection_name, ids=ids, with_payload=True, with_vectors=False):
        record = planned[str(point.id)]
        assert point.payload['user_name'] == p['cube'] and point.payload['user_id'] == p['user']
        assert point.payload['memory'] == record['memory'] and point.payload['memory_type'] == record['nativeType']
        assert point.payload['status'] == 'activated' and point.payload['acceptance_run'] == p['run']
    missing = []
    for r in p['records']:
        if r['id'] in existing:
            node = existing[r['id']]
            assert node['user_name'] == p['cube'] and node['user_id'] == p['user']
            assert node['memory'] == r['memory'] and node['memory_type'] == r['nativeType'] and node['status'] == 'activated'
        else:
            missing.append(r)
    stage = 'real-embedding'
    vectors = []
    if missing:
        embed_cfg = APIConfig.get_embedder_config()
        assert embed_cfg['backend'] == 'universal_api'
        embed_cfg['config']['backup_client'] = False
        embedder = EmbedderFactory.from_config(EmbedderConfigFactory.model_validate(embed_cfg))
        vectors = embedder.embed([r['memory'] for r in missing])
        assert len(vectors) == len(missing)
    stage = 'owned-sdk-write'
    for r, vector in zip(missing, vectors):
        assert len(vector) == int(os.getenv('EMBEDDING_DIMENSION', '1024'))
        info = {'acceptance_run': p['run']}
        if r['type'] == 'event_mem': info['dashboard_type'] = 'event_mem'
        if r['type'] == 'skill_mem': info['skill_name'] = p['run'] + '-format-notes'
        metadata = TreeNodeTextualMemoryMetadata(user_id=p['user'], session_id=p['run'] + '-sdk', memory_type=r['nativeType'], status='activated', embedding=vector, info=info)
        item = TextualMemoryItem(id=r['id'], memory=r['memory'], metadata=metadata)
        graph.add_node(item.id, item.memory, item.metadata.model_dump(), user_name=p['cube'])
    stage = 'native-storage-readback'
    with graph.driver.session(database=graph.db_name) as session:
        rows = {r['id']: r['node'] for r in session.run('MATCH (n:Memory) WHERE n.id IN $ids RETURN n.id AS id, n AS node', ids=ids)}
    points = {str(r.id): r for r in graph.vec_db.client.retrieve(collection_name=graph.vec_db.config.collection_name, ids=ids, with_payload=True, with_vectors=True)}
    assert set(rows) == set(ids) == set(points)
    for r in p['records']:
        node, point = rows[r['id']], points[r['id']]
        assert node['user_name'] == p['cube'] and node['user_id'] == p['user']
        assert node['memory_type'] == r['nativeType'] and node['status'] == 'activated'
        assert node['memory'] == r['memory'] and node['vector_sync'] == 'success'
        assert point.payload['user_name'] == p['cube'] and point.payload['memory_type'] == r['nativeType']
        assert point.payload['status'] == 'activated' and point.vector
    report.update(status='PASS', records=len(ids), newRecords=len(missing), embeddingInputs=len(missing), schemaInitialization='read-only guards', graphAndVectorsVerified=True)
except Exception as error:
    report['failure'] = {'stage': stage, 'type': type(error).__name__}
finally:
    if graph is not None:
        graph.driver.close()
        graph.vec_db.client.close()
report_stream.write(json.dumps(report) + '\n')
report_stream.flush()
sys.exit(0 if report['status'] == 'PASS' else 1)
