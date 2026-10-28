"""Query construction/control flow only; does not impersonate Neo4j integration."""
import unittest
from query import parameters, statement, query
from types import SimpleNamespace

class Tests(unittest.TestCase):
    def test_bounds_and_scope(self):
        base = dict(user_id='owner', mem_cube_id='cube', tab='event_mem')
        for extra in ({'page':None}, {'page_size':1001}, {'page':True}, {'ids':[]}, {'ids':['a','a']}, {'user_id':''}, {'tab':'injection'}):
            with self.assertRaises(ValueError): parameters({**base, **extra})
        p = parameters({**base, 'page':1000, 'page_size':10})
        self.assertEqual(p['skip'],9990)
        self.assertEqual(p['size'],10)
        self.assertLess(p['preview'],4000)

    def test_all_filters_precede_storage_pagination_and_projection_is_bounded(self):
        for tab in ('text_mem','profile_mem','event_mem','pref_mem','tool_mem','skill_mem'):
            sql = statement(tab)
            self.assertIn('n.user_name = $cube AND n.user_id = $user', sql)
            self.assertLess(sql.index('CONTAINS $search'),sql.index('SKIP $skip LIMIT $size'))
            self.assertIn('count(n) AS total',sql)
            self.assertIn('substring(',sql)
            self.assertNotIn('properties(n)',sql)
            self.assertNotIn('embedding',sql)
            self.assertNotIn('RETURN n ',sql)

    def test_exact_id_and_time_validation(self):
        p = parameters(dict(user_id='u',mem_cube_id='c',tab='text_mem',ids=['x'],page=99))
        self.assertEqual((p['skip'],p['size']),(0,1))
        with self.assertRaises(ValueError):
            parameters(dict(user_id='u',mem_cube_id='c',tab='text_mem',start_time='2026-10-03T00:00:00Z',end_time='2026-10-02T00:00:00Z'))

    def test_query_checks_conflicts_and_echoes_trusted_scope(self):
        class Session:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def execute_read(self,fn): return fn(self)
            def run(self,sql,**p): return SimpleNamespace(single=lambda: dict(rows=[],total=0,conflicts=self.conflicts))
        s=Session(); s.conflicts=0
        graph=SimpleNamespace(driver=SimpleNamespace(session=lambda **kw:s),db_name='fixture')
        b=dict(user_id='u',mem_cube_id='c',tab='text_mem')
        self.assertEqual(query(graph,b),dict(rows=[],total=0,user_id='u',mem_cube_id='c'))
        s.conflicts=1
        with self.assertRaises(ValueError): query(graph,b)

if __name__ == '__main__': unittest.main()
