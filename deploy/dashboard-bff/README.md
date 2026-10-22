# Deploying the live Dashboard backend

Use the MemOS service and MCP gateway from this repository's `deployment` branch. The extensions
were developed against its `5ab3443d31303c4ca202759c69a72ff59f480ed4` layout (`/app/src/memos`
and `/gateway`). Review compatibility before updating the base: patch scripts check source anchors
and must fail rather than silently patch unknown code. Model and embedding configuration must work.

This directory contains runtime modules, image overlays and opt-in verification tools. Workstation
SSH relays, model-account repairs and one-off machine deployment scripts are not part of the feature.

## BFF image and state

Build from the repository root:

```bash
docker build -t memos-dashboard-bff:local -f deploy/dashboard-bff/Dockerfile .
```

Configure a private Compose environment file (never commit real values):

```dotenv
MEMOS_DOCKER_NETWORK=your-existing-memos-network
MEMOS_API_BASE_URL=http://memos:8000
MEMOS_SERVICE_URL=http://127.0.0.1:8003
MEMOS_CUBE_OWNER=your-existing-owner
DASHBOARD_WORKSPACES=[]
DASHBOARD_AUDIT_DIR=/absolute/path/to/gateway-audit
DASHBOARD_STATE_DIR=/absolute/path/to/dashboard-state
DASHBOARD_PUBLISHED_PORT=5281
```

Create and permission the host directories first. Add private extension tokens as described below,
then run `docker compose --env-file /path/to/private.env -f deploy/dashboard-bff/compose.yml up -d --build`.
The example uses an existing network, read-only root filesystem, dropped capabilities, read-only audit
mount and loopback-only published port. Its health check also verifies native MemOS health. Preserve
the state volume and use one BFF writer per state directory.

**There is no BFF administrator authentication.** Use authenticated private access or an SSH forward,
not a public `0.0.0.0` binding. CORS and workspace mappings do not provide administrator authentication.

## Required selected-ID soft deletion

The original product delete endpoint can physically delete nodes. The BFF therefore requires the
private extension for deletion and never falls back to that endpoint:

```bash
docker build --build-arg MEMOS_BASE_IMAGE=your-memos-base:tag \
  -t memos-dashboard:soft-delete deploy/dashboard-bff/memory-extension
```

Use the resulting image for the existing MemOS service, preserving its environment, mounts and database
connections. Set `DASHBOARD_MEMORY_ENABLED=1` and a generated server-only `DASHBOARD_MEMORY_TOKEN`
of at least 32 characters on MemOS, and the same token on the BFF. See [retention semantics](memory-extension/README.md).

## Optional scoped Prompt support

To install both extensions, build the Prompt layer first and soft-delete on top:

```bash
docker build --build-arg MEMOS_BASE_IMAGE=your-memos-base:tag \
  -t memos-dashboard:prompts deploy/dashboard-bff/prompt-extension
docker build --build-arg MEMOS_BASE_IMAGE=memos-dashboard:prompts \
  -t memos-dashboard:extended deploy/dashboard-bff/memory-extension
```

MemOS needs `DASHBOARD_PROMPT_ENABLED=1`, a persistent private `DASHBOARD_PROMPT_DIR`, and a
`DASHBOARD_PROMPT_TOKEN` of at least 32 characters. Set the same token on the BFF. Configuration is
keyed by user/cube. See [supported scope](../../docs/PROMPTS.md). Native preference production is
configured separately; this extension does not automatically enable or relabel it.

## Gateway keys and trusted audit attribution

Mount `gateway-keys/` read-only at `/dashboard-keys` in the existing gateway image and use
`python /dashboard-keys/entrypoint.py` as its command. This composes the original tools, identity
resolver and audit sink with a file-backed verifier and trusted audit decorator. Preserve the
gateway's existing backend/model configuration and audit volume.

Mount a private directory containing `gateway.env` into **both** gateway and BFF: read-only on
the gateway, read-write on the BFF. Set `DASHBOARD_GATEWAY_ENV` on both to the file's container
path. Add these mounts through a local Compose override; the basic Compose example does not enable them.

The file contains a valid native `MEMOS_TENANTS` mapping: strong token keys map to a user string
or an object with `user_id` and `cube_id`. Use restrictive host permissions; never commit tokens.
Mount the **directory**, not the individual file, because updates use atomic rename. An empty mapping
keeps authentication enabled and denies every token. Missing/corrupt configuration fails closed.

New tool calls receive authenticated `cube_id` and public `api_key_id`. Legacy logs are not rewritten
or guessed from current mappings. This decorator is required for correct project/key statistics,
independently of whether key-creation UI is used.

## Verification and upgrades

Start with health and read-only REST/audit comparisons. Real mutating tests require dedicated fixtures
and explicit consent; see [testing](../../docs/testing.md). Key and preference verification tools require
`DASHBOARD_TEST_BFF` set to an origin reachable from their gateway container. They may create and clean
their own fixtures; read each tool's stated scope first.

Keep the previous image and protected state backup. Test extension guards against the exact native
image; do not change deletion semantics, disable authentication/TLS or restart unrelated databases
to make a test pass. Rollout/rollback orchestration is an operator responsibility, not a hidden
side effect of this portable Compose example.
