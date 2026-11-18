from __future__ import annotations

import json

from ducklake_s3_benchmark.config import PreflightConfig
from ducklake_s3_benchmark.preflight import collect_local_preflight

from test_config import valid_config


def test_local_preflight_is_redacted_and_makes_no_network_probe() -> None:
    config = PreflightConfig.from_mapping(valid_config())

    report = collect_local_preflight(config)
    encoded = json.dumps(report, sort_keys=True)

    assert report["schema_version"] == 1
    assert report["state"] == "passed"
    assert report["network_probe_performed"] is False
    assert report["runtime"]["duckdb"] == "1.5.5"
    assert config.s3_bucket not in encoded
    assert config.approved_prefix not in encoded
    assert config.work_root not in encoded


def test_version_mismatch_stops_local_preflight() -> None:
    payload = valid_config()
    payload["expected_duckdb_version"] = "99.0.0"
    config = PreflightConfig.from_mapping(payload)

    report = collect_local_preflight(config)

    assert report["state"] == "stopped"
    assert report["checks"]["duckdb_version"] is False
