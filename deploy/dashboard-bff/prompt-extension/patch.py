"""Guarded build-time integration: no runtime monkey-patching or original checkout edits."""
import ast
import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "/app/src/memos")
updates = {}


def replace(text, before, after):
    if text.count(before) != 1:
        raise RuntimeError("Upstream source drift: expected exactly one patch anchor")
    return text.replace(before, after)


p = root / "mem_reader/multi_modal_struct.py"
s = p.read_text(encoding="utf-8")
s = replace(s, '        prompt_type: str = "chat",\n    ) -> dict:',
            '        prompt_type: str = "chat",\n        dashboard_rules: str = "",\n        dashboard_strict: bool = False,\n    ) -> dict:')
s = replace(s, '        messages = [{"role": "user", "content": prompt}]\n        try:',
            '        from memos.dashboard_prompts.runtime import scoped_prompt, validate_extraction\n'
            '        prompt = scoped_prompt(prompt, dashboard_rules)\n'
            '        messages = [{"role": "user", "content": prompt}]\n        try:')
s = replace(s, '            response_json = parse_json_result(response_text)\n        except Exception as e:',
            '            response_json = parse_json_result(response_text)\n'
            '            if dashboard_strict:\n                validate_extraction(response_json)\n'
            '        except Exception as e:\n            if dashboard_strict:\n'
            '                raise RuntimeError("Scoped extraction failed") from None')
# Custom/debug responses may contain sensitive text. Do not emit them even through preview helpers.
s = replace(s, '        # 同上：messages 是刚打过的同一条 prompt，response_json 是抽取出的记忆正文。',
            '        if dashboard_strict:\n            return response_json\n'
            '        # 同上：messages 是刚打过的同一条 prompt，response_json 是抽取出的记忆正文。')
updates[p] = s

p = root / "multi_mem_cube/single_cube.py"
s = p.read_text(encoding="utf-8")
s = replace(s, '            memories_local = self.mem_reader.get_memory(\n                [add_req.messages],',
            '            from memos.dashboard_prompts.runtime import read_with_prompt\n'
            '            memories_local = read_with_prompt(\n                self.mem_reader, [add_req.messages],')
updates[p] = s

p = root / "api/server_api.py"
s = p.read_text(encoding="utf-8")
s = replace(s, 'plugin_manager.init_app(app)',
            'plugin_manager.init_app(app)\n\nfrom memos.dashboard_prompts.api import install as install_dashboard_prompts\n'
            'install_dashboard_prompts(app, server_router_module.components["mem_reader"])')
updates[p] = s

# Validate every anchor and syntax before changing any file.
for p, s in updates.items():
    ast.parse(s)
for p, s in updates.items():
    p.write_text(s, encoding="utf-8")
    print(str(p.relative_to(root)), hashlib.sha256(s.encode()).hexdigest())
