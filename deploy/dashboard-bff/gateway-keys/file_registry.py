"""Read-only tenant snapshots shared with the BFF's atomic GatewayConfigAdapter.

The native registry remains the identity/claims authority. Never fall back to an
old snapshot or process environment when the configured file is missing/invalid.
"""
import json
import re
from pathlib import Path


class ConfigurationError(ValueError):
    pass


def tenant_value(text):
    matches = re.findall(r'^MEMOS_TENANTS=([^\r\n]*)\r?$', text, re.M)
    if len(matches) != 1:
        raise ConfigurationError('Exactly one tenant mapping is required')
    value = matches[0].strip()
    if value.startswith(("'", '"')):
        quote = value[0]
        pattern = r"^'((?:\\.|[^'\\])*)'\s*(?:#.*)?$" if quote == "'" else r'^"((?:\\.|[^"\\])*)"\s*(?:#.*)?$'
        match = re.fullmatch(pattern, value)
        if not match:
            raise ConfigurationError('Invalid quoted tenant mapping')
        value = match[1]
        escapes = {'\\': '\\', "'": "'", '"': '"', 'a': '\a', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t', 'v': '\v'}
        value = re.sub(r"\\(['\\])" if quote == "'" else r'''\\([\\'"abfnrtv])''', lambda m: escapes[m[1]], value)
    if not value:
        raise ConfigurationError('Tenant mapping must be explicit, including empty mappings')
    # Validate identities before the native parser, whose errors can include tokens.
    if value.startswith('{'):
        try:
            entries = json.loads(value)
            if not isinstance(entries, dict):
                raise ValueError()
            for token, spec in entries.items():
                spec = {'user_id': spec} if isinstance(spec, str) else spec
                if not isinstance(spec, dict):
                    raise ValueError()
                for part in (token, spec.get('user_id'), spec.get('cube_id') or spec.get('user_id')):
                    if not isinstance(part, str) or not part.strip():
                        raise ValueError()
        except (ValueError, TypeError):
            raise ConfigurationError('Invalid tenant identities') from None
    return value


class FileTenantRegistry:
    def __init__(self, path, registry_type):
        self.path = Path(path)
        self.registry_type = registry_type

    def validate(self):
        try:
            with self.path.open('rb') as source:
                data = source.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError()
            return self.registry_type.from_raw(tenant_value(data.decode('utf-8')))
        except (OSError, ValueError, TypeError, AttributeError):
            raise ConfigurationError('Gateway tenant configuration unavailable or invalid') from None

    def snapshot(self):
        try:
            return self.validate()
        except ConfigurationError:
            return self.registry_type.from_raw('{}')

    def __len__(self):
        return len(self.snapshot())

    def get(self, token):
        return self.snapshot().get(token)

    def user_id_of(self, token):
        return self.snapshot().user_id_of(token)

    def verifier_tokens(self):
        return self.snapshot().verifier_tokens()
