"""Deterministic aliases and recursive redaction for downloadable evidence."""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping


_SENSITIVE_MARKERS = (
    "access_key",
    "account",
    "authorization",
    "bucket",
    "credential",
    "password",
    "prefix",
    "role_arn",
    "secret",
    "session_token",
    "token",
    "work_root",
)
_SECRET_MARKERS = (
    "access_key",
    "authorization",
    "credential",
    "password",
    "secret",
    "session_token",
    "token",
)


def alias(value: str, salt: str) -> str:
    digest = sha256(f"{salt}\0{value}".encode("utf-8")).hexdigest()[:16]
    return f"alias-{digest}"


def _redact_value(value: Any, salt: str) -> Any:
    if isinstance(value, Mapping):
        return redact_mapping(value, salt=salt)
    if isinstance(value, list):
        return [_redact_value(item, salt) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item, salt) for item in value]
    if isinstance(value, str) and value.lower().startswith("s3://"):
        return f"s3://redacted/{alias(value, salt)}"
    return value


def redact_mapping(payload: Mapping[str, Any], *, salt: str) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        lowered = str(key).lower()
        if any(marker in lowered for marker in _SECRET_MARKERS):
            redacted[str(key)] = "<redacted>"
        elif any(marker in lowered for marker in _SENSITIVE_MARKERS):
            redacted[str(key)] = alias(str(value), salt)
        else:
            redacted[str(key)] = _redact_value(value, salt)
    return redacted
