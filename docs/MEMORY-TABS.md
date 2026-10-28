# Memory views

| Dashboard view | Native evidence |
| --- | --- |
| Facts (`text_mem`) | Native textual memories, including explicitly marked event/profile records |
| Preferences (`pref_mem`) | Native preference bucket/type |
| Events (`event_mem`) | Native `Context` / `InsightMemory`, or explicit supported event marker |
| Profile tree (`profile_mem`) | Native `UserMemory`, or explicit supported profile marker; not an automatically synthesized tree |
| Tools (`tool_mem`) | Native tool-schema memories |
| Skills (`skill_mem`) | Native skill memories |

The six counts are not disjoint: event/profile records also belong to the textual view.
Do not relabel generic stored conversations as preferences merely to make the tabs nonempty.
SDK-seeded typed fixtures prove read mapping and storage, not automatic extraction of every type.

Native `memory_type` is retained as `memoryType` in the public projection. `WorkingMemory`
and `LongTermMemory` alone do not imply an event. Contradictory explicit/native labels fail
closed instead of silently moving records between views. This revises the previous explicit-marker-only
mapping, which excluded naturally stored UserMemory/Context/InsightMemory records.

Production reads now require the private `/product/dashboard-memory/query` extension.
It applies user+cube ownership, active status, tab, search and time filters before storage-side
count/order/pagination. Only a bounded projection crosses the network; details and ownership
checks query exact IDs. No fallback to `page:null` exports is allowed. The supported implementation
uses Neo4j Community `Memory.user_name` (cube) and `Memory.user_id` (owner), matching the native
writer and soft-delete guard. Unsupported storage returns an explicit error rather than inferred ownership.

Pagination and search apply to the selected workspace/view. Exact IDs remain the basis for details
and deletion. A list preview may be shorter than the native content, but a detail response must
preserve its full text. After deletion, list/detail disappearance alone is insufficient evidence:
verify the original node/vector remains with deleted status and compare an undeleted control.
