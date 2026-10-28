import contextvars
import inspect
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from scoped_audit import ScopedAuditSink


class Sink:
    def __init__(self):
        self.rows = []

    def write(self, entry):
        self.rows.append(entry)


class ScopeTests(unittest.TestCase):
    def test_key_identity_comes_only_from_verified_claims(self):
        sink = Sink()
        identity = SimpleNamespace(current=lambda: SimpleNamespace(user_id='owner', cube_id='A'))
        key = 'a' * 24
        token = SimpleNamespace(claims={'dashboard_api_key_id': key})
        entry = {'api_key_id': 'spoof', 'input': {'api_key_id': 'spoof'}, 'ok': False}
        ScopedAuditSink(sink, identity, lambda: token).write(entry)
        self.assertEqual(sink.rows[0]['api_key_id'], key)
        self.assertFalse(sink.rows[0]['ok'])
        ScopedAuditSink(sink, identity).write(entry)
        self.assertNotIn('api_key_id', sink.rows[1])
        token.claims['dashboard_api_key_id'] = 'invalid'
        ScopedAuditSink(sink, identity, lambda: token).write(entry)
        self.assertNotIn('api_key_id', sink.rows[2])
        self.assertEqual(entry['api_key_id'], 'spoof')

    def test_trusted_identity_wins_and_input_is_not_mutated(self):
        sink = Sink()
        identity = SimpleNamespace(current=lambda: SimpleNamespace(user_id='owner', cube_id='A'))
        entry = {'tenant': 'spoof', 'cube_id': 'spoof', 'input': {'cube_id': 'B'}, 'ok': False}
        ScopedAuditSink(sink, identity).write(entry)
        self.assertEqual(sink.rows[0]['cube_id'], 'A')
        self.assertEqual(sink.rows[0]['tenant'], 'owner')
        self.assertFalse(sink.rows[0]['ok'])
        self.assertEqual(entry['cube_id'], 'spoof')

    def test_missing_identity_stays_unattributed(self):
        def unavailable():
            raise RuntimeError('No context')
        sink = Sink()
        ScopedAuditSink(sink, SimpleNamespace(current=unavailable)).write({'tenant': 'owner', 'cube_id': 'spoof'})
        self.assertNotIn('cube_id', sink.rows[0])

    def test_parallel_callers_do_not_share_mutable_identity(self):
        caller = contextvars.ContextVar('caller')
        sink = Sink()
        scoped = ScopedAuditSink(sink, SimpleNamespace(current=caller.get),
                                 lambda: SimpleNamespace(claims={'dashboard_api_key_id': f'{int(caller.get().cube_id):024x}'}))
        def write(i):
            token = caller.set(SimpleNamespace(user_id='owner', cube_id=str(i)))
            try:
                scoped.write({'index': i})
            finally:
                caller.reset(token)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(100)))
        self.assertEqual(len(sink.rows), 100)
        self.assertTrue(all(r['cube_id'] == str(r['index']) for r in sink.rows))
        self.assertTrue(all(r['api_key_id'] == f'{r["index"]:024x}' for r in sink.rows))

    def test_native_wrapper_preserves_schema_result_and_failed_audit(self):
        try:
            from gateway.audit.tool_log import ToolLogger
        except ImportError:
            self.skipTest('Native gateway integration requires gateway image')
        calls, searches = Sink(), Sink()
        identity = SimpleNamespace(current=lambda: SimpleNamespace(user_id='owner', cube_id='B'))
        logger = ToolLogger(calls=ScopedAuditSink(calls, identity), searches=ScopedAuditSink(searches, identity), peek_tenant=lambda: 'owner')
        def example(query: str, limit: int = 2) -> str:
            if query == 'fail':
                raise ValueError('fixture')
            return 'result'
        wrapped = logger.wrap(example)
        self.assertEqual(inspect.signature(wrapped), inspect.signature(example))
        self.assertEqual(wrapped('ok'), 'result')
        with self.assertRaises(ValueError):
            wrapped('fail')
        self.assertEqual([r['cube_id'] for r in calls.rows], ['B', 'B'])
        self.assertEqual([r['ok'] for r in calls.rows], [True, False])


if __name__ == '__main__':
    unittest.main()
