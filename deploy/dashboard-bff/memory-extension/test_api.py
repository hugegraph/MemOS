"""Native-image FastAPI auth/contract smoke tests, not real storage acceptance."""
import os
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from memos.dashboard_memory.api import install
from test_soft_delete import Neo4jCommunityGraphDB


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.graph = Neo4jCommunityGraphDB()
        self.token = 'fixture-private-' + 'x' * 40
        with patch.dict(os.environ, DASHBOARD_MEMORY_ENABLED='1', DASHBOARD_PROMPT_TOKEN=self.token):
            app = FastAPI()
            install(app, self.graph)
        self.client = TestClient(app)
        self.body = {'user_id': 'owner', 'mem_cube_id': 'A', 'memory_ids': ['one']}
        self.headers = {'X-Dashboard-Prompt-Token': self.token}
    def test_no_auth_or_bad_auth_never_writes(self):
        for headers in ({}, {'X-Dashboard-Prompt-Token': 'bad'}):
            self.assertEqual(self.client.post('/product/dashboard-memory/soft-delete', json=self.body, headers=headers).status_code, 403)
        self.assertEqual(self.graph.nodes['one']['status'], 'activated')
    def test_scope_failure_leaves_entire_batch_unchanged(self):
        body = {**self.body, 'memory_ids': ['one', 'absent']}
        self.assertEqual(self.client.post('/product/dashboard-memory/soft-delete', json=body, headers=self.headers).status_code, 404)
        self.assertEqual(self.graph.nodes['one']['status'], 'activated')
    def test_models_auth_and_missing_runtime_fail_closed(self):
        for headers in ({}, {'X-Dashboard-Prompt-Token': 'bad'}):
            self.assertEqual(self.client.get('/product/dashboard-memory/models', headers=headers).status_code, 403)
        reply = self.client.get('/product/dashboard-memory/models', headers=self.headers)
        self.assertEqual(reply.status_code, 501)
        self.assertNotIn(self.token, reply.text)
    def test_explicit_success_and_sync_failure_semantics(self):
        self.graph.vec_db.fail = True
        self.assertEqual(self.client.post('/product/dashboard-memory/soft-delete', json=self.body, headers=self.headers).status_code, 503)
        self.graph.vec_db.fail = False
        response = self.client.post('/product/dashboard-memory/soft-delete', json=self.body, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['soft_deleted'], True)


if __name__ == '__main__': unittest.main()
