from __future__ import annotations

import json

from ducklake_s3_benchmark.config import PreflightConfig
from ducklake_s3_benchmark.redaction import alias, redact_mapping

from test_config import valid_config


def test_alias_is_stable_but_salt_scoped() -> None:
    assert alias("bucket-a", "salt-a") == alias("bucket-a", "salt-a")
    assert alias("bucket-a", "salt-a") != alias("bucket-a", "salt-b")
    assert "bucket-a" not in alias("bucket-a", "salt-a")


def test_redacts_sensitive_fields_and_s3_uris_recursively() -> None:
    raw = {
        "bucket": "approved-staging-bucket",
        "prefix": "research-enable/ducklake-tests/",
        "role_arn": "arn:aws:iam::123456789012:role/private-role",
        "nested": {
            "uri": "s3://approved-staging-bucket/research-enable/ducklake-tests/file.parquet",
            "authorization": "secret-value",
        },
    }

    redacted = redact_mapping(raw, salt="campaign-salt")
    encoded = json.dumps(redacted, sort_keys=True)

    for forbidden in (
        "approved-staging-bucket",
        "research-enable",
        "123456789012",
        "private-role",
        "secret-value",
    ):
        assert forbidden not in encoded
    assert redacted["nested"]["uri"].startswith("s3://redacted/")


def test_config_public_view_never_exposes_location() -> None:
    config = PreflightConfig.from_mapping(valid_config())

    encoded = json.dumps(config.public_view(), sort_keys=True)

    assert config.s3_bucket not in encoded
    assert config.approved_prefix not in encoded
    assert config.work_root not in encoded
    assert config.campaign_id in encoded
