# Real-service acceptance

Missing configuration or driver capability is BLOCKED, not successful real acceptance.
Exit codes: 0 PASS, 1 FAIL, 2 BLOCKED. Never run the destructive full driver against an
interactive/manual fixture or a cleaned manifest.

## Generic HTTP runner

Copy `manifest.example.json` into a private `.data` path and replace every CONFIGURE value.
`manifest.schema.json` documents the format; runtime safety checks impose additional restrictions.

```bash
pnpm test:issue:safety
pnpm test:issue:http --config .data/acceptance/manifest.json
```

The read-only runner compares real REST IDs/content/state and bounded local JSONL snapshots.
Without a live backend it reports blocked/failure, not mock acceptance. Its gateway, key, Prompt
and full browser phases remain BLOCKED unless their actual drivers execute. Optional mutations
require `--allow-mutation`, `mutation.enabled=true`, a dedicated empty workspace and a unique
run prefix. Cleanup owns returned IDs only; ambiguous results never authorize broad deletion.

## Explicit SSH/Docker profile

The full driver supports an authorized Linux SSH target with Docker and compatible native images.
Configure these variables; none are inferred from a developer workstation:

| Variable | Meaning |
| --- | --- |
| `ISSUE_SSH_TARGET` | Authorized SSH alias or user@host, with working BatchMode authentication |
| `ISSUE_MEMOS_CONTAINER` | Dedicated native MemOS container |
| `ISSUE_GATEWAY_CONTAINER` | Corresponding gateway |
| `ISSUE_BFF_CONTAINER` | Corresponding BFF |
| `ISSUE_DOCKER_HOST` | Optional explicit Unix socket; otherwise the remote Docker default/context |
| `ISSUE_COMPOSE_PROJECT` | Expected ownership label before any stop |
| `ISSUE_MEMOS_SERVICE` | Expected Compose service label |
| `ISSUE_MEMOS_IMAGE_ID` | Exact running `sha256:...` image identity, not a mutable tag |
| `ISSUE_CHROME` | Installed Chromium/Chrome/Edge executable |
| `ISSUE_HEADFUL` | `1` for visible native-input validation |

Prepare fresh dedicated workspaces using `deploy/dashboard-bff/verify-workspaces.ts` and its
documented environment configuration; read its `prepare`/`--allow-write` safeguards first.

Create an empty `.data/acceptance` directory and set `WORKSPACE_VERIFY_MANIFEST` to a new file
within it. Set `WORKSPACE_VERIFY_BFF`, `WORKSPACE_VERIFY_UPSTREAM`, `WORKSPACE_VERIFY_OWNER`,
`WORKSPACE_VERIFY_PROTECTED_PROJECT` and `WORKSPACE_VERIFY_PROTECTED_USER_ID` to the dedicated
deployment and its protected control identity. Then execute:

```bash
node deploy/dashboard-bff/verify-workspaces.ts prepare --allow-write
```

The resulting `batch3-...` ledger must have `state=prepared`, a protected original selection,
`protectedUserId`, explicit `owner`, and loopback `bff`/`upstream` origins. The protected Dashboard
project ID and native cube ID must match for this driver. Do not adapt old fixtures by changing
their status fields to pretend they are fresh.

Set `ISSUE_WORKSPACE_MANIFEST` to that ledger and `ISSUE_NATIVE_SDK=ssh-docker`, then run
`node scripts/issue-acceptance/real/prepare-native-types.ts --allow-write`. These four typed SDK
records establish read mapping, not automatic producer coverage. Build the frontend before running:

```bash
pnpm build
pnpm test:issue:real --profile ssh-docker --manifest .data/acceptance/workspaces.json --allow-write --allow-service-stop
```

The full driver obtains fresh authenticated MCP/JSONL evidence, verifies native reads, runs browser
phases and finally soft-deletes its owned records and revokes their Dashboard mappings. It does
**not** preserve fixtures for manual use. Outage tests validate container/project/service/image
identity and restore the same service with an EXIT trap and bounded watchdog. No shared daemon
or database container is part of that target.

Check client/server clocks before correlating logs. Native snapshot checks target Neo4j Community
and the supported `neo4j_vec_db` Qdrant collection. The full planned gate still exposes known
frontend omissions; selected green tests do not replace it. Transport configuration has local
safety tests; actual disruptive acceptance must be rerun in a dedicated configured environment
before claiming deployment validation. External PASS files never substitute for execution.
