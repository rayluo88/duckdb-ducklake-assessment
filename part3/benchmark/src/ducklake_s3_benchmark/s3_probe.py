"""One-object, create-only S3 permission and Region probe."""

from __future__ import annotations

from hashlib import sha256
from pathlib import PurePosixPath
import re
import secrets
from typing import Any

from .config import ConfigError, PreflightConfig
from .redaction import alias


_PROBE_ID_RE = re.compile(r"^[A-Za-z0-9-]{3,64}$")
_PROBE_BODY = b"ducklake-part3-preflight-v1\n"


def build_campaign_key(config: PreflightConfig, relative_key: str) -> str:
    if not isinstance(relative_key, str) or not relative_key or relative_key.startswith("/"):
        raise ConfigError("relative object key must be non-empty and relative")
    if "\\" in relative_key or "//" in relative_key:
        raise ConfigError("relative object key contains an unsafe separator")
    parts = PurePosixPath(relative_key).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ConfigError("relative object key cannot traverse the campaign prefix")
    key = config.campaign_prefix + relative_key
    if not key.startswith(config.campaign_prefix):
        raise ConfigError("object key escaped the campaign prefix")
    return key


def _is_missing(error: BaseException) -> bool:
    response = getattr(error, "response", {})
    code = str(response.get("Error", {}).get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}


def _bucket_region(response: dict[str, Any]) -> str:
    location = response.get("LocationConstraint")
    if location in (None, ""):
        return "us-east-1"
    if location == "EU":
        return "eu-west-1"
    return str(location)


def run_s3_probe(
    config: PreflightConfig,
    client: Any,
    *,
    confirmation: bool,
    probe_id: str | None = None,
) -> dict[str, Any]:
    if not config.allow_remote_probe:
        raise ConfigError("remote probe is not enabled in the active configuration")
    if not confirmation:
        raise ConfigError("explicit live-probe confirmation is required")

    selected_probe_id = probe_id or secrets.token_hex(12)
    if not _PROBE_ID_RE.fullmatch(selected_probe_id):
        raise ConfigError("probe_id must contain only letters, numbers and hyphens")
    key = build_campaign_key(config, f"preflight/{selected_probe_id}.txt")
    salt = config.campaign_id

    region = _bucket_region(client.get_bucket_location(Bucket=config.s3_bucket))
    region_match = region == config.expected_region
    report: dict[str, Any] = {
        "schema_version": 1,
        "stage": "s3-prefix-probe",
        "state": "stopped",
        "bucket_alias": alias(config.s3_bucket, salt),
        "campaign_prefix_alias": alias(config.campaign_prefix, salt),
        "probe_key_alias": alias(key, salt),
        "region": region,
        "region_match": region_match,
        "round_trip_match": False,
        "cleanup_confirmed": False,
    }
    if not region_match:
        return report

    client.list_objects_v2(
        Bucket=config.s3_bucket,
        Prefix=config.campaign_prefix,
        MaxKeys=1,
    )
    try:
        client.head_object(Bucket=config.s3_bucket, Key=key)
    except BaseException as error:
        if not _is_missing(error):
            raise
    else:
        report["stop_reason"] = "probe destination already exists"
        return report

    uploaded = False
    version_id: str | None = None
    try:
        put_response = client.put_object(
            Bucket=config.s3_bucket,
            Key=key,
            Body=_PROBE_BODY,
            IfNoneMatch="*",
            Metadata={"purpose": "ducklake-part3-preflight"},
        )
        uploaded = True
        version_id = put_response.get("VersionId")
        head = client.head_object(Bucket=config.s3_bucket, Key=key)
        received = client.get_object(Bucket=config.s3_bucket, Key=key)["Body"].read()
        round_trip_match = (
            received == _PROBE_BODY
            and head.get("ContentLength") == len(_PROBE_BODY)
        )
        report.update(
            {
                "round_trip_match": round_trip_match,
                "content_sha256": sha256(received).hexdigest(),
                "versioning_observed": bool(version_id or head.get("VersionId")),
                "etag_observed": bool(head.get("ETag")),
            }
        )
        if not round_trip_match:
            report["stop_reason"] = "probe round trip did not match"
    finally:
        if uploaded:
            delete_arguments: dict[str, Any] = {
                "Bucket": config.s3_bucket,
                "Key": key,
            }
            if version_id:
                delete_arguments["VersionId"] = version_id
            client.delete_object(**delete_arguments)
            try:
                client.head_object(Bucket=config.s3_bucket, Key=key)
            except BaseException as error:
                if _is_missing(error):
                    report["cleanup_confirmed"] = True
                else:
                    raise

    if report["round_trip_match"] and report["cleanup_confirmed"]:
        report["state"] = "passed"
    return report
