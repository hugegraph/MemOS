"""Guard first install, upgrade and repeated overlay without changing live files."""
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

ANCHOR = 'install_dashboard_prompts(app, server_router_module.components["mem_reader"])'
IMPORT = 'from memos.dashboard_memory.api import install as install_dashboard_memory'
OLD = 'install_dashboard_memory(app, server_router_module.graph_db)'
NEW = 'install_dashboard_memory(app, server_router_module.graph_db, server_router_module.components)'


class PatchTests(unittest.TestCase):
    def apply(self, text):
        with patch.object(Path, 'read_text', return_value=text), patch.object(Path, 'write_text') as write:
            runpy.run_path(str(Path(__file__).with_name('patch.py')))
        return write.call_args.args[0]

    def test_install_upgrade_and_repeat(self):
        for text in [ANCHOR, ANCHOR + '\n' + IMPORT + '\n' + OLD, ANCHOR + '\n' + IMPORT + '\n' + NEW]:
            result = self.apply(text)
            self.assertEqual(result.count(IMPORT), 1)
            self.assertEqual(result.count(NEW), 1)
            self.assertEqual(result.count(OLD), 0)

    def test_unknown_or_duplicate_anchors_fail(self):
        for text in ['', ANCHOR + '\n' + ANCHOR, ANCHOR + '\n' + IMPORT + '\ninstall_dashboard_memory(app, unknown)']:
            with self.assertRaises(AssertionError): self.apply(text)

    def test_drift_and_invalid_syntax_never_write(self):
        for text in ['', ANCHOR + '\n' + ANCHOR, ANCHOR + '\nnot valid python :']:
            with patch.object(Path, 'read_text', return_value=text), patch.object(Path, 'write_text') as write:
                with self.assertRaises((AssertionError, SyntaxError)):
                    runpy.run_path(str(Path(__file__).with_name('patch.py')))
                write.assert_not_called()

    def test_extension_images_require_verified_base_digest(self):
        for directory in ('memory-extension','prompt-extension'):
            dockerfile = Path(__file__).resolve().parent.parent / directory / 'Dockerfile'
            self.assertIn('@sha256:', dockerfile.read_text())


if __name__ == '__main__': unittest.main()
