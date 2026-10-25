# Validation

## Local deterministic checks

Use Node 22.18+ (CI is configured for Node 24). The BFF and its colocated tests are
TypeScript, executed with Node's native type stripping like the upstream mock. Execution
does not typecheck: `pnpm typecheck` checks both the existing TS projects and the explicit
strict `tsconfig.live.json`, including BFF tests, and `tsconfig.tools.json` for all Node
acceptance, browser and deployment scripts. Both enforce erasable syntax; no loader or
JavaScript fallback is added. The original frontend configuration and Python extensions
remain unchanged. Scripts sent to Node through stdin use `--input-type=module-typescript`.

```bash
pnpm install --frozen-lockfile
pnpm verify
node scripts/issue-acceptance/verify-frontend-unchanged.ts
node --test server/live/*.test.ts scripts/mock-api.test.ts scripts/acceptance.test.ts scripts/issue-acceptance/isolated/*.test.ts scripts/issue-acceptance/contract/*.test.ts scripts/issue-acceptance/real/*.test.ts
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
# Runtime model endpoint and current upstream browser checks

The frozen root OpenAPI document still defines 37 operations (including 11 permitted placeholders).
The live-only additive model endpoint has its own contract in `server/live/models.openapi.json` and
regressions in `server/live/models.test.ts`. It requires the upgraded private native memory extension;
do not serve static model names merely to make the original models page stop returning404.

The browser runner now checks the481a291 skeleton/localized empty/error states, project readiness,
badge-plus-name picker and icon actions. Repeated workspace selection may use an independently observed
successful response for that exact workspace/view; exact native IDs/content and DOM are still checked.
`UI-MODELS-CONTENT` exercises memory/chat tabs, names/roles, and actual model-name clipboard readback.
For real verification, `ISSUE_MODELS_ORACLE` can point to a credential-free native inventory JSON
`{ "records": [...] }` captured independently from the BFF.

`ISSUE_WAIVE_LIVE_BADGE=1` is an **explicit opt-in for the missing label only**, requiring prior approval.
The case is recorded `WAIVED`, not `PASS`; live metadata and absence of sample-data fallback must still
succeed. Other frontend omissions remain `BLOCKED`; missing routes and upstream errors remain failures.
Original TODO placeholders are not converted to PASS by these tests or by historical acceptance reports.
