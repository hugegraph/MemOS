"""Offline tests: no real model, database, credentials or server startup."""
import concurrent.futures
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

root = Path(__file__).parent
package = types.ModuleType("prompt_test_package")
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
for name in ("store", "runtime"):
    spec = importlib.util.spec_from_file_location(package.__name__ + "." + name, root / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
store = sys.modules[package.__name__ + ".store"]
runtime = sys.modules[package.__name__ + ".runtime"]


class Tests(unittest.TestCase):
    def test_store_isolation_restart_reset_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            s = store.PromptStore(directory)
            self.assertEqual(s.get("owner", "a")["prompt"], "")
            self.assertEqual(s.save("owner", "a", "规则A")["revision"], 1)
            self.assertEqual(store.PromptStore(directory).get("owner", "a")["prompt"], "规则A")
            self.assertEqual(s.get("owner", "b")["prompt"], "")
            self.assertEqual(s.get("other", "a")["prompt"], "")
            snapshot = s.get("owner", "a")
            s.save("owner", "a", "")
            self.assertEqual(snapshot["prompt"], "规则A")
            self.assertEqual(s.get("owner", "a")["revision"], 2)
            s._path("owner", "a").write_text("broken")
            with self.assertRaises(RuntimeError): s.get("owner", "a")
            with self.assertRaises(RuntimeError): s.save("owner", "a", "must not replace corrupt state")

    def test_atomic_concurrent_revisions_and_path_safety(self):
        with tempfile.TemporaryDirectory() as directory:
            s = store.PromptStore(directory)
            with concurrent.futures.ThreadPoolExecutor(5) as pool:
                results = list(pool.map(lambda i: s.save("../owner", "../../cube", str(i)), range(15)))
            self.assertEqual(sorted(r["revision"] for r in results), list(range(1,16)))
            self.assertEqual(len(list(Path(directory).glob("*.json"))), 1)
            with self.assertRaises(ValueError): s.save("o", "c", "x" * 12001)

    def test_validation_and_rule_insertion_never_formats_user_braces(self):
        self.assertIn('{arbitrary}', runtime.scoped_prompt("native", '{arbitrary}'))
        self.assertEqual(runtime.scoped_prompt("native", ""), "native")
        for value in ({}, {"memory list": "not-array"}, {"memory list":[{"value":"x", "memory_type":"SkillMemory"}]}):
            with self.assertRaises(RuntimeError): runtime.validate_extraction(value)
        self.assertEqual(runtime.validate_extraction({"memory list":[],"summary":""})["memory list"], [])
        for value in ([], [{"role":"user","content":" "}], [{"role":"tool","content":"x"}]):
            with self.assertRaises(ValueError): runtime.validate_messages(value)

    def test_default_and_fast_do_not_change_reader_path(self):
        reader = types.SimpleNamespace(get_memory=lambda *a, **k: "native")
        context = types.SimpleNamespace(user_id="u", mem_cube_id="c")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DASHBOARD_PROMPT_ENABLED":"1"}):
            runtime._store = store.PromptStore(directory)
            self.assertEqual(runtime.read_with_prompt(reader, [], mode="fine", user_context=context), "native")
            runtime._store.save("u","c","rule")
            self.assertEqual(runtime.read_with_prompt(reader, [], mode="fast", user_context=context), "native")

    def test_custom_failures_propagate_before_any_memory_construction(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DASHBOARD_PROMPT_ENABLED":"1"}):
            runtime._store = store.PromptStore(directory)
            runtime._store.save("u","c","rule")
            reader = types.SimpleNamespace(_make_memory_item=lambda **k: self.fail("must not construct failed extraction"))
            with patch.object(runtime, "extract", side_effect=RuntimeError("model failed")):
                with self.assertRaises(RuntimeError):
                    runtime.read_with_prompt(reader, [[{"role":"user","content":"example"}]], mode="fine", type="chat",
                         user_context=types.SimpleNamespace(user_id="u", mem_cube_id="c"))

    def test_same_owner_parallel_cubes_receive_explicit_snapshot(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DASHBOARD_PROMPT_ENABLED":"1"}):
            runtime._store = store.PromptStore(directory)
            runtime._store.save("u","A","rule-A")
            runtime._store.save("u","B","rule-B")
            reader = types.SimpleNamespace(_make_memory_item=lambda **kwargs: kwargs)
            def extract(_reader, _messages, snapshot, _tags):
                return {"memory list":[{"value":snapshot["prompt"], "memory_type":"UserMemory"}]}, []
            def call(cube):
                return runtime.read_with_prompt(reader, [[{"role":"user","content":"input"}]], mode="fine", type="chat",
                    user_context=types.SimpleNamespace(user_id="u",mem_cube_id=cube), info={"user_id":"u"})[0][0]
            with patch.object(runtime,"extract",side_effect=extract), concurrent.futures.ThreadPoolExecutor(2) as pool:
                a,b = list(pool.map(call,["A","B"]))
            self.assertEqual(a["value"],"rule-A")
            self.assertEqual(b["value"],"rule-B")
            self.assertEqual(a["info"]["dashboard_prompt_revision"],1)

    def test_generate_is_bounded_and_does_not_save(self):
        reader=types.SimpleNamespace(llm=types.SimpleNamespace(generate=lambda _:"draft"))
        snapshot={"prompt":"old","revision":1}
        self.assertEqual(runtime.generate(reader,"change",snapshot),{"prompt":"draft"})
        self.assertEqual(snapshot["prompt"],"old")
        reader.llm.generate=lambda _:None
        with self.assertRaises(RuntimeError): runtime.generate(reader,"change",snapshot)

    @unittest.skipUnless(os.getenv("MEMOS_SOURCE"), "Set MEMOS_SOURCE to validate real upstream patch anchors")
    def test_guarded_patch_on_actual_source_and_repeat_rejected(self):
        source=Path(os.environ["MEMOS_SOURCE"])
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)
            for name in ("mem_reader/multi_modal_struct.py", "multi_mem_cube/single_cube.py", "api/server_api.py"):
                p=target/name
                p.parent.mkdir(parents=True,exist_ok=True)
                p.write_bytes((source/name).read_bytes())
            command=[sys.executable,str(root/"patch.py"),directory]
            first=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(first.returncode,0,first.stderr)
            before=(target/"multi_mem_cube/single_cube.py").read_bytes()
            second=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(second.returncode,0)
            self.assertEqual((target/"multi_mem_cube/single_cube.py").read_bytes(),before)


if __name__ == "__main__":
    unittest.main()
