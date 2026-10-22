# Live backend

The live BFF implements the original 37 Dashboard paths using MemOS REST and native
gateway audit files. It runs independently of the mock server. The frontend is not
customized; configure its API origin before starting Vite or building it.

## Local setup

Use Node 22.18+ (Node 24 is used in CI) and pnpm. Install with `pnpm install --frozen-lockfile`.
Start a compatible MemOS deployment first. Copy `.env.example` to `.env.live`, then set:

- `MEMOS_API_BASE_URL`: the private MemOS REST origin; the referenced deployment listens on 8000.
- `MEMOS_CUBE_OWNER`: an existing active ordinary user permitted to create cubes.
- `DASHBOARD_WORKSPACES`: initial explicit bindings, for example
  `[{"id":"demo","name":"Demo","userId":"demo-owner","cubeId":"demo-cube","tenant":"demo-owner","status":"ENABLED"}]`.
  These are placeholders, not automatically provisioned identities.
- `MEMORY_CALL_LOG_DIR`: the gateway directory containing `calls-*.jsonl`, readable by the BFF.
- `DASHBOARD_MEMORY_TOKEN`: the shared server-only soft-delete token, after installing the extension.

```bash
pnpm live
```

Set `VITE_API_BASE=http://127.0.0.1:5281` in `.env.local`, and optionally
`VITE_SERVICE_BASE=http://127.0.0.1:8003`. Start `pnpm dev` and open
`http://127.0.0.1:5274/cn/memoryList`. No mock process is needed. For manual acceptance,
prefer `pnpm build` followed by `pnpm preview` on 5275; the API origin is embedded at build time.

An empty initial mapping is allowed. Creating the first project requires a valid owner;
it creates a native cube and persists the Dashboard binding. A configured ID alone is not
proof that the corresponding principal exists or has permissions. For explicit provisioning,
review `deploy/dashboard-bff/bootstrap-owner.py` before running it inside MemOS.

## State and errors

`DASHBOARD_DATA_DIR` contains persistent project, key metadata and template state. Initial
environment mappings are used only when `projects.json` does not exist; changing the environment
does not overwrite persisted mappings. Use one writer per data directory and preserve it across restarts.

Health, missing upstream data and unsupported private extensions fail explicitly. The BFF
never falls back to sample memories. Missing audit storage is not equivalent to zero calls.
Revoking a Dashboard workspace removes its mapping, not the entire native cube.

## Security and optional capabilities

**This is a trusted local management BFF, not a multi-user authentication boundary.** Bind it
to loopback or place it behind an authenticated private access layer. CORS is not authentication;
workspace scoping only limits the configured management dataset. Never publicly expose the BFF,
native unauthenticated REST service, databases, or shared gateway registry.

`DASHBOARD_GATEWAY_ENV` enables key management against a shared file-backed gateway registry.
The BFF needs write access; the gateway needs read access to the same directory. Private tokens
must never use a `VITE_` name or appear in screenshots, committed files or public test reports.

Install the [deployment extensions](../deploy/dashboard-bff/README.md) for selected-ID soft deletion,
trusted cube/key attribution, key hot reload and optional workspace Prompts. Native preference
production additionally depends on native configuration and a working extraction model; successful
storage alone does not prove successful semantic extraction.
