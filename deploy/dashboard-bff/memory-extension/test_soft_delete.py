"""Offline control-flow tests. Real Neo4j/Qdrant evidence is required separately."""
import copy
import unittest
from types import SimpleNamespace
from soft_delete import delete, ScopeError, QUERY


class QdrantVecDB:
    def __init__(self):
        self.config = SimpleNamespace(collection_name='fixture')
        self.points = {i: {'id': i, 'payload': {'status': 'activated', 'memory': 'fixture'}, 'vector': [0.1, 0.2]} for i in ('one', 'two')}
        self.client = self
        self.fail = False
    def retrieve(self, **kwargs):
        return [SimpleNamespace(**copy.deepcopy(self.points[i])) for i in kwargs['ids'] if i in self.points]
    def update(self, id, data):
        if self.fail:
            raise RuntimeError('fixture vector outage')
        self.points[id]['payload'].update(data['payload'])


class Neo4jCommunityGraphDB:
    def __init__(self):
        self.db_name = 'fixture'
        self.nodes = {i: {'id': i, 'user_id': 'owner', 'user_name': 'A', 'status': 'activated', 'memory': 'original'} for i in ('one', 'two')}
        self.driver = self
        self.vec_db = QdrantVecDB()
    def session(self, **kwargs): return self
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute_write(self, work): return work(self)
    def run(self, query, **p):
        assert query == QUERY and 'DETACH DELETE' not in query
        nodes = [self.nodes[i] for i in p['ids'] if i in self.nodes and self.nodes[i]['user_id'] == p['user'] and self.nodes[i]['user_name'] == p['cube'] and self.nodes[i]['status'] in ('activated', 'deleted')]
        row = None
        if len(nodes) == p['count']:
            for node in nodes:
                node.update(status='deleted')
                node.setdefault('delete_record_id', p['record'])
                node.setdefault('delete_time', p['time'])
            row = {'count': len(nodes)}
        return SimpleNamespace(single=lambda: row)


class Tests(unittest.TestCase):
    def test_retains_nodes_content_vectors_and_other_memories(self):
        graph = Neo4jCommunityGraphDB()
        before = copy.deepcopy(graph.vec_db.points)
        result = delete(graph, 'owner', 'A', ['one'])
        self.assertEqual(result, {'deleted': 1, 'soft_deleted': True, 'vector_records': 1})
        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(graph.nodes['one']['memory'], 'original')
        self.assertEqual(graph.nodes['two']['status'], 'activated')
        self.assertEqual(graph.vec_db.points['one']['vector'], before['one']['vector'])
        self.assertEqual(graph.vec_db.points['one']['payload']['memory'], 'fixture')
        self.assertEqual(graph.vec_db.points['one']['payload']['status'], 'deleted')
        self.assertEqual(graph.vec_db.points['two'], before['two'])
    def test_all_or_nothing_scope_and_missing_ids(self):
        for user, cube, ids in [('wrong', 'A', ['one']), ('owner', 'B', ['one']), ('owner', 'A', ['one', 'absent'])]:
            graph = Neo4jCommunityGraphDB()
            before = copy.deepcopy(graph.nodes)
            with self.assertRaises(ScopeError): delete(graph, user, cube, ids)
            self.assertEqual(graph.nodes, before)
            self.assertEqual(graph.vec_db.points['one']['payload']['status'], 'activated')
    def test_invalid_batches_do_not_write(self):
        for ids in [[], ['one'] * 2, ['x'] * 101, [None], 'one']:
            graph = Neo4jCommunityGraphDB()
            with self.assertRaises(ValueError): delete(graph, 'owner', 'A', ids)
            self.assertEqual(graph.nodes['one']['status'], 'activated')
    def test_vector_failure_is_not_success_and_private_retry_is_safe(self):
        graph = Neo4jCommunityGraphDB()
        graph.vec_db.fail = True
        with self.assertRaises(RuntimeError): delete(graph, 'owner', 'A', ['one'])
        self.assertEqual(graph.nodes['one']['status'], 'deleted')
        self.assertEqual(graph.nodes['one']['memory'], 'original')
        record = graph.nodes['one']['delete_record_id']
        graph.vec_db.fail = False
        self.assertTrue(delete(graph, 'owner', 'A', ['one'])['soft_deleted'])
        self.assertEqual(graph.nodes['one']['delete_record_id'], record)


if __name__ == '__main__': unittest.main()
