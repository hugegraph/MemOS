"""Build-time guarded route integration on the already-deployed Prompt image."""
import ast
from pathlib import Path
path = Path('/app/src/memos/api/server_api.py')
text = path.read_text()
anchor = 'install_dashboard_prompts(app, server_router_module.components["mem_reader"])'
assert text.count(anchor) == 1
old = 'install_dashboard_memory(app, server_router_module.graph_db)'
new = 'install_dashboard_memory(app, server_router_module.graph_db, server_router_module.components)'
import_line = 'from memos.dashboard_memory.api import install as install_dashboard_memory'
if import_line in text:
    assert text.count(import_line) == 1
    assert text.count(old) + text.count(new) == 1
    text = text.replace(old, new)
else:
    assert 'install_dashboard_memory(' not in text
    text = text.replace(anchor, anchor + '\n' + import_line + '\n' + new)
ast.parse(text)
path.write_text(text)
