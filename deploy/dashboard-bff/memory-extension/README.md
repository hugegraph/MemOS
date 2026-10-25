# Private selected-ID soft deletion

`POST /product/dashboard-memory/soft-delete` is installed only with `DASHBOARD_MEMORY_ENABLED=1`.
It requires `X-Dashboard-Prompt-Token` (the existing private-header name), matching `DASHBOARD_MEMORY_TOKEN` (or its
Prompt-token fallback), with at least 32 characters. Tokens stay server-side, never in `VITE_*`.

The BFF validates a batch against one configured workspace/view. The native transaction verifies
all requested IDs/user/cube before mutation; a mismatch changes no graph rows. Only deletion
status/history changes: nodes, edges and content remain. Qdrant retains vectors and payload,
updating deletion status.

This is not a distributed transaction. Vector failure after a graph update is an error, not success.
Recovery must verify both stores and retry only known scoped IDs. The private operation is idempotent;
the public BFF may return 404 after its reads hide deleted rows. There is no hard-delete fallback
or generic restore UI. The original MCP/product deletion operation is intentionally unchanged.

List disappearance alone is insufficient evidence: compare retained node/vector/content fingerprints,
scope and an undeleted positive control. SDK type fixtures prove reads, not LLM extraction.

Build with `--build-arg MEMOS_BASE_IMAGE=your-compatible-image:tag` as shown in the parent
[guide](../README.md). Run Python tests in the matching native environment. Real guard, seed and
reconciliation tools must never run against unowned data.

## Read-only runtime model inventory

The same authenticated extension now provides `GET /product/dashboard-memory/models`, consumed by
the BFF's additive `GET /api/dashboard/models` endpoint. Rebuild this layer and the BFF together.
`patch.py` supports first installation over the tested Prompt layer and a guarded upgrade over the
previous memory layer; it fails on unknown integration anchors, rather than registering duplicate routes.

The handler reads **already initialized native component instances**, not a static hand-written list,
the BFF's environment, a provider's full model catalog, or assumed Kubernetes ConfigMaps. It does not
initialize another model, perform inference, probe provider availability, or write storage. The response
is an explicit whitelist: model, role, source component path, group, and non-secret class/dimension info.
No config serialization, credential, endpoint URL, request headers or provider response is returned.

Only initialized chat registries populate the chat tab; disabled chat has no invented default rows.
Local cosine reranking has no model row. An initialized preference slot is labeled accordingly; it
does not prove the preference producer is enabled or that inference succeeds. Required core instances
missing/unsupported, missing token, or an old native image fail explicitly without sample fallback.
