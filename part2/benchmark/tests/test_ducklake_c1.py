import json
from hashlib import sha256

import pytest

from ducklake_benchmark.ducklake_c1 import (
    semantic_schema,
    configure_ducklake_c1_connection,
    read_ducklake_c1_manifest,
    read_ducklake_c1_plan,
)


def _manifest(tmp_path) -> dict[str, object]:
    catalog = tmp_path / "metadata.ducklake"
    catalog.write_bytes(b"ducklake-catalog")
    return {
        "stage_id": "P2-GCE-GCS-DUCKLAKE-C1-1GB-20260809-001",
        "bucket": "ducklake-example-project-data",
        "bucket_region": "asia-southeast1",
        "source_b1": {
            "stage_id": "P2-GCE-GCS-B1-1GB-20260809-001",
            "object": {
                "object": "gcs-b1-20260809/b1/data.parquet",
                "generation": "103",
                "sha256": "b" * 64,
                "physical_bytes": 131,
                "crc32c": "source-crc",
            },
        },
        "a0_lane_1": {
            "object": "gcs-b1-20260809/a0-lane-1/data.parquet",
            "generation": "101",
            "sha256": "a" * 64,
            "physical_bytes": 100,
            "crc32c": "a0-1",
        },
        "a0_lane_2": {
            "object": "gcs-b1-20260809/a0-lane-2/data.parquet",
            "generation": "102",
            "sha256": "a" * 64,
            "physical_bytes": 100,
            "crc32c": "a0-2",
        },
        "candidate": {
            "layout_id": "C1",
            "management_mode": "registered_byte_identical_copy",
            "object": {
                "object": "gcs-ducklake-c1-20260809/c1/data.parquet",
                "generation": "104",
                "sha256": "b" * 64,
                "physical_bytes": 131,
                "crc32c": "source-crc",
            },
        },
        "catalog": {
            "catalog_type": "duckdb_local_single_client",
            "metadata_path": str(catalog),
            "metadata_sha256": sha256(catalog.read_bytes()).hexdigest(),
            "catalog_alias": "c1",
            "schema": "main",
            "table": "b1",
            "read_only_during_timing": True,
        },
    }


def test_c1_manifest_requires_a_byte_identical_ducklake_owned_copy(tmp_path) -> None:
    path = tmp_path / "c1.json"
    path.write_text(json.dumps(_manifest(tmp_path)))

    manifest = read_ducklake_c1_manifest(path)

    assert manifest.candidate.layout_id == "C1"
    assert manifest.candidate.management_mode == "registered_byte_identical_copy"
    assert manifest.candidate.object.sha256 == manifest.source_b1.sha256
    assert manifest.catalog.relation_sql == "c1.main.b1"


def test_c1_bootstrap_plan_enforces_the_same_byte_identity_before_catalogue_creation(tmp_path) -> None:
    path = tmp_path / "c1-plan.json"
    path.write_text(json.dumps(_manifest(tmp_path)))

    plan = read_ducklake_c1_plan(path)

    assert plan.source_b1_stage_id == "P2-GCE-GCS-B1-1GB-20260809-001"
    assert plan.candidate.object.crc32c == plan.source_b1.crc32c


def test_semantic_schema_ignores_equivalent_absent_and_explicit_null_defaults() -> None:
    direct = [("row_id", "BIGINT", "YES", None, None, None)]
    ducklake = [("row_id", "BIGINT", "YES", None, "NULL", None)]

    assert semantic_schema(direct) == semantic_schema(ducklake)


def test_c1_manifest_rejects_a_rewritten_candidate_even_if_the_schema_would_match(tmp_path) -> None:
    payload = _manifest(tmp_path)
    payload["candidate"]["object"]["physical_bytes"] = 130
    path = tmp_path / "c1.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="byte-identical"):
        read_ducklake_c1_manifest(path)


def test_c1_connection_setup_loads_only_explicit_extensions_and_attaches_read_only(
    tmp_path, monkeypatch
) -> None:
    manifest_path = tmp_path / "c1.json"
    manifest_path.write_text(json.dumps(_manifest(tmp_path)))
    manifest = read_ducklake_c1_manifest(manifest_path)
    calls: list[str] = []

    class Connection:
        def execute(self, sql):
            calls.append(sql)

    httpfs = tmp_path / "httpfs.duckdb_extension"
    ducklake = tmp_path / "ducklake.duckdb_extension"
    httpfs.write_bytes(b"httpfs")
    ducklake.write_bytes(b"ducklake")
    monkeypatch.setattr(
        "ducklake_benchmark.ducklake_c1.configure_duckdb_gcs_oauth",
        lambda *args, **kwargs: calls.append("gcs-oauth"),
    )

    configure_ducklake_c1_connection(
        Connection(),
        manifest=manifest,
        httpfs_extension=httpfs,
        ducklake_extension=ducklake,
    )

    assert calls[0] == "gcs-oauth"
    assert calls[1] == "SET autoinstall_known_extensions = false"
    assert calls[2] == "LOAD '" + str(ducklake.resolve()) + "'"
    assert calls[3] == "SET autoload_known_extensions = false"
    assert calls[4].startswith("ATTACH 'ducklake:")
    assert " AS c1 (READ_ONLY)" in calls[4]
