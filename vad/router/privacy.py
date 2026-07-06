import hashlib
import json
from typing import Any


SENSITIVE_KEYS = {"prompt", "input", "message", "messages", "content", "api_key", "secret", "token"}


def redact_provider_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return _redact(payload)


def redaction_digest(payload: dict[str, Any]) -> str:
    redacted = redact_provider_payload(payload)
    serialized = json.dumps(redacted, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _redact(value):
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key.lower() in SENSITIVE_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
