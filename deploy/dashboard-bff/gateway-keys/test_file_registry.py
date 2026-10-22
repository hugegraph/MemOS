import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from file_registry import FileTenantRegistry, ConfigurationError, tenant_value


def native_registry():
    # Test the original parser, not a copied fake; explicit source path on dev host.
    source = Path(os.environ['GATEWAY_SOURCE']) / 'gateway/domain/tenants.py'
    spec = importlib.util.spec_from_file_location('native_gateway_tenants', source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.TenantRegistry


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'gateway.env'
        self.registry = FileTenantRegistry(self.path, native_registry())

    def write(self, mapping):
        raw = json.dumps(mapping).replace('\\', '\\\\').replace("'", "\\'")
        temp = self.path.with_suffix('.tmp')
        temp.write_text("OTHER=preserved\nMEMOS_TENANTS='" + raw + "'\n", encoding='utf-8')
        temp.replace(self.path)

    def test_atomic_rotation_revokes_old_and_preserves_other(self):
        self.write({'old': {'user_id': 'A', 'cube_id': 'cube-A'}, 'other': 'B'})
        self.assertEqual(self.registry.get('old').cube_id, 'cube-A')
        self.write({'new': {'user_id': 'A', 'cube_id': 'cube-A'}, 'other': 'B'})
        self.assertIsNone(self.registry.get('old'))
        self.assertEqual(self.registry.user_id_of('new'), 'A')
        self.assertEqual(self.registry.user_id_of('other'), 'B')
        self.assertEqual(self.registry.verifier_tokens()['new']['cube_id'], 'cube-A')

    def test_missing_corrupt_duplicate_empty_mapping_fail_closed(self):
        self.write({'old': 'A'})
        for text in ['', 'MEMOS_TENANTS=broken', 'MEMOS_TENANTS={}\nMEMOS_TENANTS={}', 'MEMOS_TENANTS={"secret-token": {}}']:
            self.path.write_text(text)
            self.assertIsNone(self.registry.get('old'))
            with self.assertRaises(ConfigurationError) as caught:
                self.registry.validate()
            self.assertNotIn('secret-token', str(caught.exception))
        self.path.unlink()
        self.assertEqual(self.registry.verifier_tokens(), {})

    def test_revoke_last_does_not_enable_unauthenticated_access(self):
        self.write({'only': 'A'})
        self.write({})
        self.assertEqual(len(self.registry), 0)
        self.assertIsNone(self.registry.get('only'))
        self.assertEqual(self.registry.verifier_tokens(), {})

    def test_bff_quote_escaping_preserves_exact_tokens(self):
        self.write({"quote'back\\slash": {'user_id': 'A', 'cube_id': 'B'}})
        self.assertEqual(self.registry.get("quote'back\\slash").cube_id, 'B')
        self.assertEqual(tenant_value('MEMOS_TENANTS="a:A,b:B" # comment'), 'a:A,b:B')


if __name__ == '__main__':
    unittest.main()
