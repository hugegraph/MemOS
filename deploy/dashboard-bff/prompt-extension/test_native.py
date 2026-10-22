"""Run in built image BEFORE deployment, with fake LLM and no external services."""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from memos.mem_reader.multi_modal_struct import MultiModalStructMemReader
from memos.dashboard_prompts import runtime
from memos.dashboard_prompts.api import install
from memos.dashboard_prompts.store import PromptStore


class Options:
    def with_options(self, **options):
        assert options == {"timeout": 60.0, "max_retries": 0}
        return self


class OpenAILLM:
    def __init__(self):
        self.client = Options()
        self.fail = False
        self.requests = []
        self.use_backup_client = True

    def generate(self, messages):
        self.requests.append(messages)
        if self.fail:
            raise RuntimeError("sensitive provider failure text")
        return json.dumps({"memory list": [{"key":"fixture","value":"native fixture", "memory_type":"UserMemory", "tags":[]}], "summary":""})


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env = patch.dict(os.environ, {"DASHBOARD_PROMPT_ENABLED":"1", "DASHBOARD_PROMPT_TOKEN":"unit-only-token-" + "x"*32, "DASHBOARD_PROMPT_DIR":self.directory.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        runtime._store = PromptStore(self.directory.name)
        self.reader = object.__new__(MultiModalStructMemReader)
        self.reader.config = SimpleNamespace(remove_prompt_example=False)
        self.reader.llm = OpenAILLM()

    def test_actual_native_prompt_receives_rules_and_strict_failure_has_no_fallback(self):
        result,_=runtime.extract(self.reader,[{"role":"user","content":"fixture"}],{"prompt":"ONLY-THIS-CUBE", "revision":1})
        self.assertEqual(result["memory list"][0]["value"],"native fixture")
        self.assertIn("ONLY-THIS-CUBE", self.reader.llm.requests[-1][0]["content"])
        self.assertTrue(self.reader.llm.use_backup_client) # clone only
        self.reader.llm.fail = True
        with self.assertRaisesRegex(RuntimeError,"no fallback"):
            runtime.extract(self.reader,[{"role":"user","content":"fixture"}],{"prompt":"rules", "revision":1})

    def test_private_routes_scope_no_debug_writes_and_failure_status(self):
        app=FastAPI()
        install(app,self.reader)
        with TestClient(app) as client:
            path="/product/dashboard-prompt/"
            scope={"user_id":"same-owner","mem_cube_id":"A"}
            self.assertEqual(client.post(path+"detail",json=scope).status_code,403)
            headers={"X-Dashboard-Prompt-Token":os.environ["DASHBOARD_PROMPT_TOKEN"]}
            self.assertEqual(client.post(path+"save",headers=headers,json={**scope,"prompt":"rule-A"}).status_code,200)
            other=client.post(path+"detail",headers=headers,json={**scope,"mem_cube_id":"B"}).json()
            self.assertEqual(other["data"]["prompt"],"")
            before=runtime.store().get("same-owner","A")
            result=client.post(path+"debug/extract",headers=headers,json={**scope,"messages":[{"role":"user","content":"sample"}]})
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()["data"]["results"][0]["content"],"native fixture")
            self.assertEqual(runtime.store().get("same-owner","A"),before)
            self.reader.llm.fail=True
            failure=client.post(path+"debug/extract",headers=headers,json={**scope,"messages":[{"role":"user","content":"sample"}]})
            self.assertEqual(failure.status_code,503)
            self.assertNotIn("sensitive",failure.text)
            client.post(path+"reset-default",headers=headers,json=scope)
            self.assertEqual(runtime.store().get("same-owner","A")["prompt"],"")

    def test_normal_add_adapter_builds_native_items_with_real_metadata_and_provenance(self):
        runtime.store().save("owner", "cube-A", "select the fixture")
        self.reader.embedder = SimpleNamespace(embed=lambda texts: [[0.1, 0.2] for _ in texts])
        result = runtime.read_with_prompt(self.reader, [[{"role":"user","content":"fixture source"}]],
            type="chat", mode="fine", info={"user_id":"owner","session_id":"fixture-session"},
            user_context=SimpleNamespace(user_id="owner",mem_cube_id="cube-A"))
        self.assertEqual(len(result[0]),1)
        item=result[0][0]
        self.assertEqual(item.memory,"native fixture")
        self.assertEqual(item.metadata.user_id,"owner")
        self.assertEqual(item.metadata.info["dashboard_prompt_revision"],1)
        self.assertEqual(item.metadata.embedding,[0.1,0.2])
        self.assertEqual(item.metadata.sources[0].content,"fixture source")


if __name__ == "__main__":
    unittest.main()
