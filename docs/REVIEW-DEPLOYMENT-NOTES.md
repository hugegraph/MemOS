# Review fixes: deployment and recovery boundaries

## Upgrade order

1. Choose the supported Neo4j Community deployment. Capture the actual native base image's registry digest;
   build arguments must use `registry/image@sha256:<verified 64-hex digest>`. Do not invent a digest or use a mutable tag.
2. Build the Prompt overlay against its verified native base; build the memory overlay against the verified
   Prompt-overlay image digest. The guarded source patch still fails closed on anchor/syntax drift.
3. Run extension tests in the matching image. Validate `/product/dashboard-memory/query` with the private
   token and explicit user/cube, including native event/profile records, pagination, filters and exact IDs.
4. Upgrade BFF and share the same `DASHBOARD_MEMORY_TOKEN`. Old extensions or missing configuration now fail explicitly;
   they are never silently replaced by unbounded `/product/get_memory` exports.

The query implementation targets Neo4j Community only, just as the existing delete extension explicitly targets
Neo4j Community + Qdrant. Graph predicates use the exact trusted `user_name=cubeId AND user_id=userId` tuple.
Text search/count may still scan matching nodes inside the database; this is bounded network transfer, not a claim
of constant-time indexed full-text search. Large individual detail bodies remain subject to the response safety bound.

## Identity migration

`tenant` must equal the authenticated gateway `userId`. An inconsistent imported workspace fails startup rather than
silently showing zero audit usage. Correct the stored binding against the actual gateway identity; never relabel
historical audit records or relax cube isolation just to make a list nonempty.

## Persistence failures

Use one BFF writer per data directory. Template writes use unique temporary files, flush + atomic rename, schema
validation and stale-state detection. They do not claim distributed multi-writer transactions.

Key creation persists metadata before gateway activation; a failed gateway write compensates metadata. Revocation
never reactivates credentials if metadata cleanup fails. An inert leftover metadata row can be removed after checking
that its hash has no active registry entry. No token is written to the review report.

Workspace creation records the planned cube identity in `workspace-create-recovery.json` **before** contacting the
upstream. After confirmed native creation, the journal becomes `upstream-confirmed`. If local registration fails,
retrying the same name completes local registration without creating another cube, including after process restart.
An ambiguous timeout leaves `planned`: stop blind retries, inspect that exact owned cube upstream, then reconcile the
journal. Never delete unrelated cubes or assume a timeout means no native resource exists. Automatic reconciliation
of an ambiguous native outcome is deliberately not claimed.

## Validation evidence

`review-*.test.ts` cover protocol bounds, unmarked native shapes, Unicode clusters, diagnostics and persistence faults.
`verify_native_query.py` is an opt-in disposable loopback Neo4j regression with 10,006 nodes and >32 MiB source content.
It checks actual Cypher execution and is wired into a separate CI service job. It is **not** a deployed MemOS/Qdrant
or browser end-to-end acceptance test. Missing local Docker/native services are BLOCKED, not PASS.

The frontend fingerprint gate is unchanged. Audit API diagnostics and backend fixes do not imply a new UI banner.
The old public OpenAPI Project schema still requires a name; BFF has an explicit status-only validator compatible
with the frozen shipped UI, without synthesizing a placeholder name.
