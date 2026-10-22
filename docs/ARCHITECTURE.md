# Architecture and compatibility

```text
Unmodified Dashboard -- VITE_API_BASE --> server/live BFF
                                           |-- MemOS REST --> Neo4j + Qdrant
                                           |-- native calls-*.jsonl
                                           |-- scoped local management state
                                           `-- shared gateway key registry
server/mock remains a separate development backend
```

The BFF owns contract validation, DTO projection, pagination/search, workspace mappings,
JSONL normalization and daily usage. It is not 37 new native MemOS endpoints. The approved
frontend baseline is `481a291e24928fc5eab5fb051b5c653fafa9cbe4`; its 67-file fingerprint gate covers
`src`, `public`, shared frontend contracts and entry/build configuration without formatting exceptions.

## Memory and deletion

Memory IDs and content originate from MemOS. List projections omit vectors and large source/history
payloads, use bounded previews and a response budget; detail requests retain the full content.
See [memory tabs](MEMORY-TABS.md) for overlapping views and explicit classification requirements.

Dashboard deletion calls a private, authenticated, selected-ID soft-delete extension. It retains
graph nodes and vectors, marking both deleted. It never falls back to the native legacy product
delete route, which can physically remove nodes. The existing MCP/legacy deletion path is unchanged.
Cross-store updates are not a distributed transaction: vector update failure is surfaced, not
reported as success; an operator must inspect and retry the exact scoped IDs.

## Audit and usage

The gateway reuses native tools, timing and JSONL sinks. A decorator records authenticated
`tenant`, `cube_id` and `api_key_id`; client payloads cannot establish these identities.
The public key ID is the first 24 hex characters of SHA-256 of the UTF-8 token, matching the
BFF key service. The raw token is not added to audit records.

Project queries match both tenant and historical cube. Legacy rows without cube attribution are
not assigned using today's registry, even if a tenant has only one current workspace. They remain
visible in the configured-tenant global query with explicit unknown-attribution metadata.

Usage is aggregated by UTC date. `totalCalls` includes all normalized events and failed tool calls;
`callsByEvent` exposes their breakdown. Empty days contain zero counts, not phantom events.
`apiKeyId` filters trusted key IDs; revoked-key history remains queryable within still-visible
workspaces. Unknown IDs return zero, without revealing foreign data. Unkeyed history is never
divided between current keys. `unattributedRecords` and `unattributedKeyRecords` describe missing
cube/key provenance and are not disjoint totals to add together.

## Deliberate extensions and boundaries

- MemOS: private selected-ID soft-delete module and API registration.
- Optional MemOS Prompt support: scoped configuration plus narrow integration into synchronous
  plaintext fine extraction; see [Prompt scope](PROMPTS.md).
- Gateway: file-backed verifier/registry and trusted audit decoration, composed through an
  independent entrypoint rather than rewriting native business tools.

These are real upstream runtime extensions, not merely frontend configuration. Knowledge-base
and document APIs use the 11 placeholders explicitly permitted by Issue #1; a successful placeholder
write does not claim data was stored. OpenAPI documents their narrowly scoped empty/null shapes.

The newer frontend includes graph/model requests outside the original 37-path contract; they are
not implemented by this BFF. Existing frontend limitations also include the live badge, some filters,
usage controls/full-event display and the template UI. The backend supports more than the current
usage page renders. These limitations are not hidden by changing the frontend or test expectations.
