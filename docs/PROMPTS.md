# Scoped Prompt configuration

Prompt APIs are an optional extension. Enable `DASHBOARD_PROMPT_ENABLED=1`, configure a persistent
`DASHBOARD_PROMPT_DIR`, and set the same strong `DASHBOARD_PROMPT_TOKEN` in MemOS and the BFF.
The extension checks its supported reader/client at startup and fails closed on incompatible code.

Configuration is scoped by user and cube. Generation returns a draft; saving persists it. Debug
extraction uses the saved configuration and does not write memories. Reset removes the override;
an empty override means the native default, not an unavailable service.

Supported normal-write integration is **synchronous plaintext fine chat extraction**. Fast mode,
asynchronous jobs, multimodal paths and other producers are not claimed as globally overridden.
The extension copies model handles for bounded requests rather than mutating a process-wide template.
Model failures are surfaced by management operations; do not treat a native fallback conversation
save as proof the requested extraction semantics worked.

Validate separately: save/read/restart persistence, debug behavior, actual normal-write effect,
cross-workspace isolation and reset. A successful debug response alone does not prove normal writes
use the configuration. Template CRUD is separate BFF management state; it does not establish that
all native profile-tree generation pipelines consume templates.
