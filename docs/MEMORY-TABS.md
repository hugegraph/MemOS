# Memory views

| Dashboard view | Native evidence |
| --- | --- |
| Facts (`text_mem`) | Native textual memories, including explicitly marked event/profile records |
| Preferences (`pref_mem`) | Native preference bucket/type |
| Events (`event_mem`) | Explicit supported event marker, not a guess from prose |
| Profile tree (`profile_mem`) | Explicit supported profile marker, not an automatically synthesized tree |
| Tools (`tool_mem`) | Native tool-schema memories |
| Skills (`skill_mem`) | Native skill memories |

The six counts are not disjoint: event/profile records also belong to the textual view.
Do not relabel generic stored conversations as preferences merely to make the tabs nonempty.
SDK-seeded typed fixtures prove read mapping and storage, not automatic extraction of every type.

Pagination and search apply to the selected workspace/view. Exact IDs remain the basis for details
and deletion. A list preview may be shorter than the native content, but a detail response must
preserve its full text. After deletion, list/detail disappearance alone is insufficient evidence:
verify the original node/vector remains with deleted status and compare an undeleted control.
