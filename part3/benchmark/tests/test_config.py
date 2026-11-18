from __future__ import annotations

import json

import pytest

from ducklake_s3_benchmark.config import ConfigError, PreflightConfig, load_config


def valid_config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "campaign_id": "p3-20260824-smoke",
        "expected_region": "ap-southeast-1",
        "s3_bucket": "approved-staging-bucket",
        "approved_prefix": "research-enable/ducklake-tests/",
        "work_root": "/home/sagemaker-user/ducklake-p3",
        "expected_duckdb_version": "1.5.5",
        "allow_remote_probe": False,
    }


def test_loads_strict_valid_config(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(valid_config()), encoding="utf-8")

    config = load_config(path)

    assert isinstance(config, PreflightConfig)
    assert config.campaign_id == "p3-20260824-smoke"
    assert config.campaign_prefix == (
        "research-enable/ducklake-tests/ducklake-p3/p3-20260824-smoke/"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("campaign_id", "../escape"),
        ("expected_region", "us-east"),
        ("s3_bucket", "s3://approved-staging-bucket"),
        ("s3_bucket", "REPLACE-WITH-BUCKET"),
        ("approved_prefix", "one-segment/"),
        ("approved_prefix", "/absolute/prefix/"),
        ("approved_prefix", "research/../other/"),
        ("approved_prefix", "REPLACE/WITH-PREFIX/"),
        ("work_root", "/"),
        ("work_root", "/home/sagemaker-user"),
        ("work_root", "relative/work"),
        ("expected_duckdb_version", "1.5"),
    ],
)
def test_rejects_unsafe_or_unresolved_values(tmp_path, field, value) -> None:
    payload = valid_config()
    payload[field] = value
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize(
    "credential_key",
    ["access_key", "secret_access_key", "session_token", "password", "authorization"],
)
def test_rejects_credential_fields_even_when_values_are_blank(tmp_path, credential_key) -> None:
    payload = valid_config()
    payload[credential_key] = ""
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ConfigError, match="credential-like"):
        load_config(path)


def test_rejects_unknown_fields(tmp_path) -> None:
    payload = valid_config()
    payload["surprise"] = "value"
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ConfigError, match="unknown"):
        load_config(path)
