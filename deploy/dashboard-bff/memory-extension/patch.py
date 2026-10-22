"""Build-time guarded route integration on the already-deployed Prompt image."""
import ast
from pathlib import Path
path = Path('/app/src/memos/api/server_api.py')
text = path.read_text()
anchor = 'install_dashboard_prompts(app, server_router_module.components["mem_reader"])'
assert text.count(anchor) == 1
text = text.replace(anchor, anchor + '\nfrom memos.dashboard_memory.api import install as install_dashboard_memory\ninstall_dashboard_memory(app, server_router_module.graph_db)')
ast.parse(text)
path.write_text(text)
