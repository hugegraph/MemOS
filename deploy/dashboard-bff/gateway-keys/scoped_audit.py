"""Decorate native audit sinks with the authenticated caller's cube, never tool input.

Reuse native ToolLogger timing, failures, payload clipping and sink persistence.
No global request state: resolve within the same tool execution context.
"""
import re

class ScopedAuditSink:
    def __init__(self, sink, identity, access_token=lambda: None):
        self.sink = sink
        self.identity = identity
        self.access_token = access_token

    def write(self, entry):
        record = dict(entry)
        record.pop('cube_id', None)
        record.pop('api_key_id', None)
        try:
            caller = self.identity.current()
            if (isinstance(caller.user_id, str) and caller.user_id.strip()
                    and isinstance(caller.cube_id, str) and caller.cube_id.strip()):
                record['tenant'] = caller.user_id
                record['cube_id'] = caller.cube_id
                key_id = (getattr(self.access_token(), 'claims', None) or {}).get('dashboard_api_key_id')
                if isinstance(key_id, str) and re.fullmatch(r'[0-9a-f]{24}', key_id):
                    record['api_key_id'] = key_id
        except Exception:
            # Native logging must not change the tool result if identity is unavailable.
            # Missing cube stays unknown; never guess it from input or a registry snapshot.
            pass
        self.sink.write(record)
