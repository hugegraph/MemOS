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
