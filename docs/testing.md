# Validation

## Local deterministic checks

```bash
pnpm install --frozen-lockfile
pnpm verify
node scripts/issue-acceptance/verify-frontend-unchanged.mjs
node --test server/live/*.test.mjs scripts/mock-api.test.mjs scripts/acceptance.test.mjs scripts/issue-acceptance/isolated/*.test.mjs scripts/issue-acceptance/contract/*.test.mjs scripts/issue-acceptance/real/*.test.mjs
npx --yes @redocly/cli@2.67.1 lint openapi.json
```

Node suites use isolated fixtures/local test servers. Mock tests establish frontend/contract baselines,
not real MemOS acceptance. Historical live-acceptance TODO entries remain explicitly reported, not
counted as passes. Building or invoking all routes alone does not prove semantic or integration coverage.
OpenAPI currently retains license/localhost warnings; the upstream frontend build has a chunk-size warning.

The frontend gate compares exact Git-blob bytes. `.gitattributes` preserves LF checkouts on Windows;
do not bypass the gate by normalizing its expected hashes or formatting the protected frontend.

## Native extensions

Run Python tests in matching native images with the extension test directory mounted at `/tests`.
For the gateway, set `PYTHONPATH=/tests:/gateway` and `GATEWAY_SOURCE=/gateway`, then run
`python -m unittest test_file_registry test_scoped_audit test_verifier`. The verifier suite uses the
real gateway/FastMCP dependencies; missing native dependencies are not a passing integration test.
MemOS extension directories contain their corresponding `test_*.py` suites for the supported native image.

## Real services and UI

See [real runner setup](../scripts/issue-acceptance/real/README.md). It requires explicit deployment
configuration, fresh owned fixtures and separate write/service-stop consent. Do not execute it against
personal data or an already-cleaned manifest. The full runner performs destructive fixture cleanup.
Read-only page checks do not grant permission to stop services or delete data.

Acceptance should independently compare native REST/storage and raw JSONL with BFF/UI results:

- At least three textual pages with exact IDs, an empty workspace and a second nonempty workspace.
- Unique search, full long-content detail, list response below 200KB, and workspace isolation.
- Single/batch soft deletion with retained node/vector/content proofs and undeleted controls.
- Real authenticated MCP calls correlated with native JSONL, failed calls and all-event UTC counts.
- Two keys in one workspace, cross-scope exclusion, immediate revocation and retained history.
- A separate 10,000-row audit dataset, missing-field rendering, no-mock operation and guarded outage recovery.

Report automated, native-storage, browser-observed and user-reported evidence separately. The latest
manual memory/workspace checklist was reported passed by the operator; that does not certify unrelated
log, key or fault scenarios that were not part of that checklist. Machine-specific reports, credentials,
PIDs and raw audit files stay outside the public tree. The full planned gate can remain BLOCKED for
known unimplemented frontend controls even when selected backend/page suites pass.
