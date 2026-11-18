"""Strict configuration model for a prefix-scoped Part 3 campaign."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping


class ConfigError(ValueError):
    """Raised when a preflight configuration is unsafe or incomplete."""


_FIELDS = {
    "schema_version",
    "campaign_id",
    "expected_region",
    "s3_bucket",
    "approved_prefix",
    "work_root",
    "expected_duckdb_version",
    "allow_remote_probe",
}
_CREDENTIAL_MARKERS = (
    "access_key",
    "authorization",
    "credential",
    "password",
    "secret",
    "session_token",
)
_CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_REGION_RE = re.compile(r"^[a-z]{2}(?:-[a-z0-9]+)+-\d+$")
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _require_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} must be a non-empty string")
    return value


def _contains_placeholder(value: str) -> bool:
    return "REPLACE" in value.upper() or "<" in value or ">" in value


@dataclass(frozen=True, slots=True)
class PreflightConfig:
    """Validated non-secret configuration for one campaign."""

    schema_version: int
    campaign_id: str
    expected_region: str
    s3_bucket: str
    approved_prefix: str
    work_root: str
    expected_duckdb_version: str
    allow_remote_probe: bool

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PreflightConfig":
        if not isinstance(payload, Mapping):
            raise ConfigError("configuration must be a JSON object")

        credential_fields = sorted(
            str(key)
            for key in payload
            if any(marker in str(key).lower() for marker in _CREDENTIAL_MARKERS)
        )
        if credential_fields:
            raise ConfigError(
                "credential-like configuration fields are prohibited: "
                + ", ".join(credential_fields)
            )

        unknown = sorted(set(payload) - _FIELDS)
        missing = sorted(_FIELDS - set(payload))
        if unknown:
            raise ConfigError("unknown configuration fields: " + ", ".join(unknown))
        if missing:
            raise ConfigError("missing configuration fields: " + ", ".join(missing))

        schema_version = payload["schema_version"]
        if isinstance(schema_version, bool) or schema_version != 1:
            raise ConfigError("schema_version must be integer 1")

        campaign_id = _require_string(payload, "campaign_id")
        if not _CAMPAIGN_RE.fullmatch(campaign_id) or _contains_placeholder(campaign_id):
            raise ConfigError("campaign_id must be a resolved lowercase slug")

        expected_region = _require_string(payload, "expected_region")
        if not _REGION_RE.fullmatch(expected_region) or _contains_placeholder(expected_region):
            raise ConfigError("expected_region must be a resolved AWS Region")

        bucket = _require_string(payload, "s3_bucket")
        if (
            not _BUCKET_RE.fullmatch(bucket)
            or ".." in bucket
            or _contains_placeholder(bucket)
        ):
            raise ConfigError("s3_bucket must be a resolved DNS-style bucket name")

        prefix = _require_string(payload, "approved_prefix")
        parts = prefix.rstrip("/").split("/")
        if (
            prefix.startswith("/")
            or not prefix.endswith("/")
            or "//" in prefix
            or len(parts) < 2
            or any(part in {"", ".", ".."} for part in parts)
            or _contains_placeholder(prefix)
        ):
            raise ConfigError(
                "approved_prefix must be resolved, relative, trailing-slash terminated "
                "and at least two segments deep"
            )

        work_root = _require_string(payload, "work_root")
        root_path = PurePosixPath(work_root)
        broad_roots = {
            PurePosixPath("/"),
            PurePosixPath("/home"),
            PurePosixPath("/home/sagemaker-user"),
            PurePosixPath("/root"),
            PurePosixPath("/tmp"),
        }
        if (
            not root_path.is_absolute()
            or root_path in broad_roots
            or ".." in root_path.parts
            or _contains_placeholder(work_root)
        ):
            raise ConfigError("work_root must be a resolved, task-specific absolute path")

        expected_duckdb_version = _require_string(payload, "expected_duckdb_version")
        if not _VERSION_RE.fullmatch(expected_duckdb_version):
            raise ConfigError("expected_duckdb_version must be an exact three-part version")

        allow_remote_probe = payload["allow_remote_probe"]
        if not isinstance(allow_remote_probe, bool):
            raise ConfigError("allow_remote_probe must be boolean")

        return cls(
            schema_version=1,
            campaign_id=campaign_id,
            expected_region=expected_region,
            s3_bucket=bucket,
            approved_prefix=prefix,
            work_root=work_root,
            expected_duckdb_version=expected_duckdb_version,
            allow_remote_probe=allow_remote_probe,
        )

    @property
    def campaign_prefix(self) -> str:
        return f"{self.approved_prefix}ducklake-p3/{self.campaign_id}/"

    def public_view(self) -> dict[str, Any]:
        from .redaction import alias

        salt = self.campaign_id
        return {
            "schema_version": self.schema_version,
            "campaign_id": self.campaign_id,
            "expected_region": self.expected_region,
            "bucket_alias": alias(self.s3_bucket, salt),
            "approved_prefix_alias": alias(self.approved_prefix, salt),
            "campaign_prefix_alias": alias(self.campaign_prefix, salt),
            "work_root_alias": alias(self.work_root, salt),
            "expected_duckdb_version": self.expected_duckdb_version,
            "allow_remote_probe": self.allow_remote_probe,
        }


def load_config(path: str | Path) -> PreflightConfig:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"cannot read configuration: {error}") from error
    return PreflightConfig.from_mapping(payload)
