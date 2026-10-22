"""Run in the existing native gateway image with --network none; no model calls."""
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from entrypoint import FileTokenVerifier, FileTenantRegistry, TenantRegistry, build_file_app


class NativeVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'gateway.env'
        self.write({'old': {'user_id': 'user-A', 'cube_id': 'cube-A'}, 'other': 'user-B'})
        self.verifier = FileTokenVerifier(FileTenantRegistry(self.path, TenantRegistry))

    def write(self, mapping):
        self.path.write_text('MEMOS_TENANTS=' + json.dumps(mapping))

    async def test_native_claims_and_immediate_revocation(self):
        token = await self.verifier.verify_token('old')
        self.assertEqual(token.claims['user_id'], 'user-A')
        self.assertEqual(token.claims['cube_id'], 'cube-A')
        self.assertEqual(token.claims['dashboard_api_key_id'], hashlib.sha256(b'old').hexdigest()[:24])
        # Distinct keys for the same principal must remain distinct.
        self.write({'old': {'user_id': 'user-A', 'cube_id': 'cube-A'}, 'second': {'user_id': 'user-A', 'cube_id': 'cube-A'}})
        second = await self.verifier.verify_token('second')
        self.assertNotEqual(second.claims['dashboard_api_key_id'], token.claims['dashboard_api_key_id'])
        self.assertEqual(token.scopes, ['memory'])
        self.write({'new': {'user_id': 'user-A', 'cube_id': 'cube-A'}, 'other': 'user-B'})
        self.assertIsNone(await self.verifier.verify_token('old'))
        self.assertIsNotNone(await self.verifier.verify_token('new'))
        self.assertIsNotNone(await self.verifier.verify_token('other'))

    async def test_corrupt_and_removed_mapping_deny_without_stale_fallback(self):
        self.path.write_text('MEMOS_TENANTS=not-valid')
        self.assertIsNone(await self.verifier.verify_token('old'))
        self.path.unlink()
        self.assertIsNone(await self.verifier.verify_token('other'))

    async def test_empty_mapping_keeps_auth_installed(self):
        self.write({})
        app = build_file_app(str(self.path))
        self.assertIsNotNone(app.mcp.auth)
        self.assertIsNone(await app.mcp.auth.verify_token('anything'))


if __name__ == '__main__':
    unittest.main()
