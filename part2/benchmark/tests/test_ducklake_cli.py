import json
from hashlib import sha256

import ducklake_benchmark.cli as cli_module
from ducklake_benchmark.cli import main


def _manifest(tmp_path) -> dict[str, object]:
    catalog = tmp_path / "metadata.ducklake"
    catalog.write_bytes(b"ducklake-catalog")
    return {
        "stage_id": "P2-GCE-GCS-DUCKLAKE-C1-1GB-20260809-001",
        "bucket": "ducklake-example-project-data",
        "bucket_region": "asia-southeast1",
        "source_b1": {"stage_id": "P2-GCE-GCS-B1-1GB-20260809-001", "object": {"object": "gcs-b1-20260809/b1/data.parquet", "generation": "103", "sha256": "b" * 64, "physical_bytes": 131, "crc32c": "source-crc"}},
        "a0_lane_1": {"object": "gcs-b1-20260809/a0-lane-1/data.parquet", "generation": "101", "sha256": "a" * 64, "physical_bytes": 100, "crc32c": "a0-1"},
        "a0_lane_2": {"object": "gcs-b1-20260809/a0-lane-2/data.parquet", "generation": "102", "sha256": "a" * 64, "physical_bytes": 100, "crc32c": "a0-2"},
        "candidate": {"layout_id": "C1", "management_mode": "registered_byte_identical_copy", "object": {"object": "gcs-ducklake-c1-20260809/c1/data.parquet", "generation": "104", "sha256": "b" * 64, "physical_bytes": 131, "crc32c": "source-crc"}},
        "catalog": {"catalog_type": "duckdb_local_single_client", "metadata_path": str(catalog), "metadata_sha256": sha256(catalog.read_bytes()).hexdigest(), "catalog_alias": "c1", "schema": "main", "table": "b1", "read_only_during_timing": True},
    }


def test_ducklake_screen_cli_records_the_catalogue_increment_without_calling_it_a_layout_change(
    tmp_path, monkeypatch
) -> None:
    manifest = tmp_path / "c1.json"
    manifest.write_text(json.dumps(_manifest(tmp_path)))
    noise = tmp_path / "noise.json"
    noise.write_text(
        json.dumps(
            {
                "scope": "gce_gcs_ducklake_c1_noise",
                "stage_manifest": {"sha256": __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()},
                "noise": {"aggregate_noise_log": 0.01, "q3_noise_log": 0.02},
            }
        )
    )
    validation = tmp_path / "validation.json"
    validation.write_text(
        json.dumps(
            {
                "scope": "gce_gcs_ducklake_c1_correctness",
                "status": "validated",
                "valid": True,
                "stage_manifest": {"sha256": __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()},
            }
        )
    )
    httpfs = tmp_path / "httpfs.duckdb_extension"
    ducklake = tmp_path / "ducklake.duckdb_extension"
    httpfs.write_bytes(b"httpfs")
    ducklake.write_bytes(b"ducklake")
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli_module, "_require_gce_local_execution", lambda: None)
    monkeypatch.setattr(cli_module, "configure_ducklake_c1_connection", lambda *args, **kwargs: None)

    def fake_run(**kwargs):
        seen.update(kwargs)
        return {
            "precision_stable": True,
            "bootstrap_95_ci_log": {"lower": 0.2, "upper": 0.3},
            "q3": {"precision_stable": True, "bootstrap_95_ci_log": {"lower": 0.1, "upper": 0.2}},
            "observations": [],
            "q0": [],
        }

    monkeypatch.setattr(cli_module, "run_paired_relation_screen", fake_run)

    assert main(
        [
            "ducklake-screen", "--mode", "screen", "--output", str(tmp_path / "result"),
            "--stage-manifest", str(manifest), "--noise-result", str(noise),
            "--validation", str(validation),
            "--httpfs-extension", str(httpfs), "--ducklake-extension", str(ducklake),
        ]
    ) == 0

    result = json.loads((tmp_path / "result" / "ducklake-screen-result.json").read_text())
    assert result["ducklake_involved"] is True
    assert result["candidate"]["management_mode"] == "registered_byte_identical_copy"
    assert "layout-improvement" not in result["claim_boundary"]
    assert seen["candidate_relation_sql"] == "c1.main.b1"


def test_ducklake_bootstrap_cli_records_a_validated_catalogue_manifest(tmp_path, monkeypatch) -> None:
    plan = tmp_path / "c1-plan.json"
    plan.write_text(json.dumps(_manifest(tmp_path)))
    httpfs = tmp_path / "httpfs.duckdb_extension"
    ducklake = tmp_path / "ducklake.duckdb_extension"
    httpfs.write_bytes(b"httpfs")
    ducklake.write_bytes(b"ducklake")
    monkeypatch.setattr(cli_module, "_require_gce_local_execution", lambda: None)

    def fake_bootstrap(**kwargs):
        assert kwargs["plan_path"] == plan
        return {"status": "validated", "manifest_path": str(kwargs["output"] / "c1-stage-manifest.json")}

    monkeypatch.setattr(cli_module, "bootstrap_ducklake_c1", fake_bootstrap)

    assert main(
        [
            "ducklake-bootstrap-c1", "--plan", str(plan), "--output", str(tmp_path / "result"),
            "--httpfs-extension", str(httpfs), "--ducklake-extension", str(ducklake),
        ]
    ) == 0

    result = json.loads((tmp_path / "result" / "ducklake-bootstrap-result.json").read_text())
    assert result["status"] == "validated"
    assert result["ducklake_involved"] is True


def test_ducklake_validation_cli_requires_and_records_untimed_equivalence(tmp_path, monkeypatch) -> None:
    manifest = tmp_path / "c1.json"
    manifest.write_text(json.dumps(_manifest(tmp_path)))
    httpfs = tmp_path / "httpfs.duckdb_extension"
    ducklake = tmp_path / "ducklake.duckdb_extension"
    httpfs.write_bytes(b"httpfs")
    ducklake.write_bytes(b"ducklake")
    monkeypatch.setattr(cli_module, "_require_gce_local_execution", lambda: None)
    monkeypatch.setattr(
        cli_module,
        "validate_ducklake_c1",
        lambda **kwargs: {
            "valid": True,
            "schema_match": True,
            "query_results_match": True,
            "physical_order": {"candidate_c1_failure_rows": 0},
        },
    )

    assert main(
        [
            "ducklake-validate-c1", "--stage-manifest", str(manifest),
            "--output", str(tmp_path / "result"), "--httpfs-extension", str(httpfs),
            "--ducklake-extension", str(ducklake),
        ]
    ) == 0

    result = json.loads((tmp_path / "result" / "ducklake-validation-result.json").read_text())
    assert result["scope"] == "gce_gcs_ducklake_c1_correctness"
    assert result["status"] == "validated"
    assert result["valid"] is True


def test_ducklake_direct_clone_increment_screen_uses_the_same_object_as_its_plain_baseline(
    tmp_path, monkeypatch
) -> None:
    manifest = tmp_path / "c1.json"
    manifest.write_text(json.dumps(_manifest(tmp_path)))
    noise = tmp_path / "noise.json"
    noise.write_text(
        json.dumps(
            {
                "scope": "gce_gcs_ducklake_c1_direct_noise",
                "stage_manifest": {"sha256": __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()},
                "noise": {"aggregate_noise_log": 0.01, "q3_noise_log": 0.02},
            }
        )
    )
    validation = tmp_path / "validation.json"
    validation.write_text(
        json.dumps(
            {
                "scope": "gce_gcs_ducklake_c1_direct_correctness",
                "status": "validated",
                "valid": True,
                "stage_manifest": {"sha256": __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()},
            }
        )
    )
    httpfs = tmp_path / "httpfs.duckdb_extension"
    ducklake = tmp_path / "ducklake.duckdb_extension"
    httpfs.write_bytes(b"httpfs")
    ducklake.write_bytes(b"ducklake")
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli_module, "_require_gce_local_execution", lambda: None)
    monkeypatch.setattr(cli_module, "configure_ducklake_c1_connection", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli_module,
        "run_paired_relation_screen",
        lambda **kwargs: seen.update(kwargs) or {
            "precision_stable": True,
            "bootstrap_95_ci_log": {"lower": 0.2, "upper": 0.3},
            "q3": {"precision_stable": True, "bootstrap_95_ci_log": {"lower": 0.1, "upper": 0.2}},
            "observations": [], "q0": [],
        },
    )

    assert main(
        [
            "ducklake-screen", "--comparison", "direct_clone_increment", "--mode", "screen",
            "--output", str(tmp_path / "result"), "--stage-manifest", str(manifest),
            "--noise-result", str(noise), "--validation", str(validation),
            "--httpfs-extension", str(httpfs), "--ducklake-extension", str(ducklake),
        ]
    ) == 0

    result = json.loads((tmp_path / "result" / "ducklake-screen-result.json").read_text())
    assert result["scope"] == "gce_gcs_ducklake_c1_direct_screen"
    assert "generation=104" in seen["a0_relation_sql"]
    assert seen["candidate_relation_sql"] == "c1.main.b1"
