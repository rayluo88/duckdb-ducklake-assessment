import json
from pathlib import Path

import pytest

from ducklake_benchmark.cli import main
from ducklake_benchmark.fixture import build_local_fixture
from ducklake_benchmark.model import Scenario, SyntheticProfile
import ducklake_benchmark.cli as cli_module


def test_smoke_cli_writes_self_contained_local_evidence(tmp_path, capsys) -> None:
    output_dir = tmp_path / "smoke"
    part1_reference = Path(__file__).resolve().parents[2].parent / "part1" / "poc"

    exit_code = main(
        [
            "smoke",
            "--output",
            str(output_dir),
            "--rows",
            "2_000",
            "--seed",
            "23",
            "--scenario",
            "moderately_correlated",
            "--part1-reference",
            str(part1_reference),
        ]
    )

    assert exit_code == 0
    results_path = output_dir / "smoke-results.json"
    assert results_path.is_file()
    results = json.loads(results_path.read_text())
    assert results["scope"] == "local_synthetic_functional_only"
    assert results["working_assumption"] == "native_duckdb_reference_plus_separate_parquet"
    assert results["correctness"]["valid"] is True
    assert results["queries"]["D"] == results["queries"]["A0"]
    assert len(results["provenance"]["part1_poc_sha256"]) == 64
    assert results["query_definitions"][0]["query_id"] == "Q0"
    assert any(extension["extension_name"] == "parquet" for extension in results["environment"]["extensions"])
    assert json.loads(capsys.readouterr().out)["results"] == str(results_path)


def test_calibrate_local_cli_writes_a0_only_noise_evidence(tmp_path, capsys) -> None:
    output_dir = tmp_path / "calibration"

    exit_code = main(
        [
            "calibrate-local",
            "--output",
            str(output_dir),
            "--environment-scope",
            "local_mac",
            "--rows",
            "2_000",
            "--seed",
            "41",
            "--warmups",
            "1",
            "--initial-pairs",
            "3",
            "--pair-increment",
            "2",
            "--max-pairs",
            "3",
            "--bootstrap-samples",
            "100",
        ]
    )

    assert exit_code == 0
    results_path = output_dir / "calibration-results.json"
    results = json.loads(results_path.read_text())
    assert results["result_version"] == 2
    assert results["scope"] == "local_a0_vs_a0_noise_calibration"
    assert results["execution_environment"] == {"kind": "local_mac"}
    assert results["engine"] == "DuckDB"
    assert results["storage_format"] == "plain_parquet"
    assert results["ducklake_involved"] is False
    assert results["calibration"]["lane_integrity"]["byte_identical"] is True
    assert set(results["calibration"]["queries"]) == {
        "Q0",
        "Q1",
        "Q2a",
        "Q2b",
        "Q2c",
        "Q3",
        "Q4",
        "Q5",
        "Q6",
    }
    assert all(
        query["pair_count"] == 3
        for query in results["calibration"]["queries"].values()
    )
    assert "GCP" in results["claim_boundary"]
    assert json.loads(capsys.readouterr().out)["results"] == str(results_path)


def test_calibrate_local_cli_records_gce_local_provenance(tmp_path, capsys) -> None:
    output_dir = tmp_path / "gce-calibration"

    exit_code = main(
        [
            "calibrate-local",
            "--output",
            str(output_dir),
            "--environment-scope",
            "gce_local",
            "--rows",
            "2_000",
            "--seed",
            "43",
            "--warmups",
            "1",
            "--initial-pairs",
            "3",
            "--pair-increment",
            "2",
            "--max-pairs",
            "3",
            "--bootstrap-samples",
            "100",
        ]
    )

    assert exit_code == 0
    results = json.loads((output_dir / "calibration-results.json").read_text())
    assert results["result_version"] == 2
    assert results["scope"] == "gce_local_a0_vs_a0_noise_calibration"
    assert results["execution_environment"] == {
        "kind": "gce_local",
        "project_id": "ducklake-example-project",
        "zone": "asia-southeast1-b",
        "vm_name": "ducklake-p2-bench-01",
        "storage": "boot_pd_balanced",
    }
    assert results["ducklake_involved"] is False
    assert results["claim_boundary"].startswith("GCE-local-storage A0-versus-A0")
    assert "no GCS" in results["claim_boundary"]
    assert "no GCP" not in results["claim_boundary"]
    assert json.loads(capsys.readouterr().out)["results"].endswith(
        "calibration-results.json"
    )


def test_calibrate_gcs_cli_writes_narrow_remote_evidence(tmp_path, capsys, monkeypatch) -> None:
    manifest = tmp_path / "objects.json"
    manifest.write_text(
        json.dumps(
            {
                "bucket": "ducklake-example-project-data",
                "bucket_region": "asia-southeast1",
                "lane_1": {"object": "calibration/one.parquet", "generation": "1", "sha256": "a" * 64, "physical_bytes": 100},
                "lane_2": {"object": "calibration/two.parquet", "generation": "2", "sha256": "a" * 64, "physical_bytes": 100},
            }
        )
    )
    extension = tmp_path / "httpfs.duckdb_extension"
    extension.write_bytes(b"signed-extension-fixture")
    monkeypatch.setattr(cli_module, "configure_duckdb_gcs_oauth", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli_module,
        "calibrate_parquet_uris",
        lambda *args, **kwargs: {"lane_integrity": {"byte_identical": True}, "queries": {}},
    )

    assert main(
        [
            "calibrate-gcs",
            "--output", str(tmp_path / "result"),
            "--source-manifest", str(manifest),
            "--httpfs-extension", str(extension),
            "--seed", "53",
        ]
    ) == 0

    results = json.loads((tmp_path / "result" / "calibration-results.json").read_text())
    assert results["scope"] == "gce_gcs_a0_vs_a0_noise_calibration"
    assert results["execution_environment"]["storage"] == "gcs_standard_same_region"
    assert results["ducklake_involved"] is False
    assert results["authentication"]["method"] == "gce_metadata_oauth_bearer"
    assert "ephemeral-token" not in json.dumps(results)
    assert results["httpfs_extension"]["sha256"]


def test_gcs_screen_cli_uses_generation_pinned_bstar_sources(tmp_path, capsys, monkeypatch) -> None:
    manifest = tmp_path / "stage.json"
    manifest.write_text(
        json.dumps(
            {
                "stage_id": "P2-GCE-GCS-BSTAR-1GB-20260809-001",
                "bucket": "ducklake-example-project-data",
                "bucket_region": "asia-southeast1",
                "object_prefix": "gcs-bstar-20260809/",
                "a0_lane_1": {"object": "gcs-bstar-20260809/a0-lane-1/data.parquet", "generation": "101", "sha256": "a" * 64, "physical_bytes": 100, "crc32c": "a0-1"},
                "a0_lane_2": {"object": "gcs-bstar-20260809/a0-lane-2/data.parquet", "generation": "102", "sha256": "a" * 64, "physical_bytes": 100, "crc32c": "a0-2"},
                "candidate": {"layout_id": "Bstar", "objects": [{"object": "gcs-bstar-20260809/bstar/part_0.parquet", "generation": "103", "sha256": "b" * 64, "physical_bytes": 100, "crc32c": "b0"}]},
            }
        )
    )
    noise = tmp_path / "noise.json"
    noise.write_text(
        json.dumps(
            {
                "scope": "gce_gcs_plain_parquet_bstar_noise",
                "stage_manifest": {"path": str(manifest), "sha256": __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()},
                "noise": {"aggregate_noise_log": 0.01, "q3_noise_log": 0.02},
            }
        )
    )
    extension = tmp_path / "httpfs.duckdb_extension"
    extension.write_bytes(b"signed-extension-fixture")
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli_module, "_require_gce_local_execution", lambda: None)
    monkeypatch.setattr(cli_module, "configure_duckdb_gcs_oauth", lambda *args, **kwargs: None)

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
            "gcs-screen",
            "--mode", "screen",
            "--output", str(tmp_path / "result"),
            "--stage-manifest", str(manifest),
            "--noise-result", str(noise),
            "--httpfs-extension", str(extension),
        ]
    ) == 0

    results = json.loads((tmp_path / "result" / "gcs-screen-result.json").read_text())
    assert results["scope"] == "gce_gcs_plain_parquet_bstar_screen"
    assert results["status"] == "eligible"
    assert "generation=101" in seen["a0_relation_sql"]
    assert "generation=103" in seen["candidate_relation_sql"]
    assert json.loads(capsys.readouterr().out)["status"] == "eligible"


def test_gcs_screen_cli_records_the_approved_one_file_b1_candidate(tmp_path, monkeypatch) -> None:
    manifest = tmp_path / "stage.json"
    manifest.write_text(
        json.dumps(
            {
                "stage_id": "P2-GCE-GCS-B1-1GB-20260809-001",
                "bucket": "ducklake-example-project-data",
                "bucket_region": "asia-southeast1",
                "object_prefix": "gcs-b1-20260809/",
                "a0_lane_1": {"object": "gcs-b1-20260809/a0-lane-1/data.parquet", "generation": "101", "sha256": "a" * 64, "physical_bytes": 100, "crc32c": "a0-1"},
                "a0_lane_2": {"object": "gcs-b1-20260809/a0-lane-2/data.parquet", "generation": "102", "sha256": "a" * 64, "physical_bytes": 100, "crc32c": "a0-2"},
                "candidate": {"layout_id": "B1", "objects": [{"object": "gcs-b1-20260809/b1/data.parquet", "generation": "103", "sha256": "b" * 64, "physical_bytes": 131, "crc32c": "b1"}]},
            }
        )
    )
    noise = tmp_path / "noise.json"
    noise.write_text(
        json.dumps(
            {
                "scope": "gce_gcs_plain_parquet_b1_noise",
                "stage_manifest": {"path": str(manifest), "sha256": __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()},
                "noise": {"aggregate_noise_log": 0.01, "q3_noise_log": 0.02},
            }
        )
    )
    extension = tmp_path / "httpfs.duckdb_extension"
    extension.write_bytes(b"signed-extension-fixture")
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli_module, "_require_gce_local_execution", lambda: None)
    monkeypatch.setattr(cli_module, "configure_duckdb_gcs_oauth", lambda *args, **kwargs: None)

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
            "gcs-screen",
            "--mode", "screen",
            "--output", str(tmp_path / "result"),
            "--stage-manifest", str(manifest),
            "--noise-result", str(noise),
            "--httpfs-extension", str(extension),
        ]
    ) == 0

    results = json.loads((tmp_path / "result" / "gcs-screen-result.json").read_text())
    assert results["scope"] == "gce_gcs_plain_parquet_b1_screen"
    assert results["candidate"]["layout_id"] == "B1"
    assert "generation=103" in seen["candidate_relation_sql"]


def test_evidence_cli_registers_and_compiles_a_run(tmp_path, capsys) -> None:
    result = tmp_path / "result.json"
    result.write_text(
        json.dumps(
            {
                "scope": "functional_local",
                "claim_boundary": "No performance claim.",
                "environment": {"duckdb_version": "1.5.5"},
            }
        )
    )
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"evidence")
    register = tmp_path / "evidence" / "run-register.jsonl"

    assert main(
        [
            "register-evidence",
            "--register",
            str(register),
            "--workspace-root",
            str(tmp_path),
            "--run-id",
            "P2-TEST-001",
            "--recorded-at",
            "2026-08-09T12:00:00+08:00",
            "--run-type",
            "functional",
            "--status",
            "passed",
            "--result",
            str(result),
            "--artifact",
            str(artifact),
            "--interpretation",
            "Correctness gate passed.",
            "--command-json",
            '["ducklake-benchmark", "smoke"]',
        ]
    ) == 0
    registered = json.loads(capsys.readouterr().out)
    assert registered["run_id"] == "P2-TEST-001"
    assert registered["sequence"] == 1

    compiled = tmp_path / "compiled"
    assert main(
        [
            "compile-evidence",
            "--register",
            str(register),
            "--workspace-root",
            str(tmp_path),
            "--output",
            str(compiled),
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["runs"] == 1
    assert (compiled / "campaign-runs.md").is_file()
    assert (compiled / "campaign-runs.csv").is_file()


def test_gcp_guardrail_cli_generates_verifies_and_previews_teardown(tmp_path, capsys) -> None:
    output = tmp_path / "guardrails"
    assert main(
        [
            "gcp-guardrails-plan",
            "--output",
            str(output),
            "--generated-at",
            "2026-08-09T04:00:00Z",
        ]
    ) == 0
    planned = json.loads(capsys.readouterr().out)
    manifest = output / "guardrail-manifest.json"
    assert planned["resources_created"] is False
    assert planned["manifest"] == str(manifest)

    assert main(
        [
            "gcp-guardrails-verify",
            "--manifest",
            str(manifest),
            "--now",
            "2026-08-09T04:10:00Z",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True

    assert main(
        [
            "gcp-guardrails-teardown",
            "--manifest",
            str(manifest),
            "--now",
            "2026-08-10T04:10:00Z",
        ]
    ) == 0
    teardown = json.loads(capsys.readouterr().out)
    assert teardown["mode"] == "dry_run"
    assert teardown["resources_changed"] is False
    assert len(teardown["commands"]) == 7


def test_layout_cli_generates_and_validates_a_local_synthetic_representation(tmp_path, capsys) -> None:
    fixture = build_local_fixture(
        tmp_path / "fixture", SyntheticProfile(rows=500, seed=29, scenario=Scenario.SHUFFLED)
    )
    import hashlib

    runtime_identity = tmp_path / "runtime-identity.json"
    runtime_identity.write_text(json.dumps({"valid": True}))
    contract = tmp_path / "a0-contract.json"
    contract.write_text(
        json.dumps(
            {
                "valid": True,
                "source_paths": {"database": str(fixture.database_path), "a0": str(fixture.parquet_path)},
                "source": {
                    "database": {"bytes": fixture.database_path.stat().st_size, "sha256": hashlib.sha256(fixture.database_path.read_bytes()).hexdigest()},
                    "a0": {"bytes": fixture.parquet_path.stat().st_size, "sha256": hashlib.sha256(fixture.parquet_path.read_bytes()).hexdigest()},
                },
                "runtime_identity": {"path": str(runtime_identity), "sha256": hashlib.sha256(runtime_identity.read_bytes()).hexdigest()},
            }
        )
    )
    output = tmp_path / "B1"

    assert main(
        [
            "layout-generate",
            "--layout",
            "B1",
            "--a0-contract",
            str(contract),
            "--source-table",
            "synthetic_data",
            "--output",
            str(output),
        ]
    ) == 0
    generated = json.loads(capsys.readouterr().out)
    manifest_path = output / "layout-manifest.json"
    assert generated["manifest"] == str(manifest_path)

    validation_output = tmp_path / "validation.json"
    assert main(
        ["layout-validate", "--manifest", str(manifest_path), "--output", str(validation_output)]
    ) == 0
    assert json.loads(validation_output.read_text())["status"] == "validated"
    assert json.loads(capsys.readouterr().out)["result"] == str(validation_output)


def test_a0_contract_cli_requires_valid_runtime_identity_and_records_provenance(tmp_path, capsys) -> None:
    fixture = build_local_fixture(
        tmp_path / "fixture", SyntheticProfile(rows=500, seed=31, scenario=Scenario.SHUFFLED)
    )
    runtime_identity = tmp_path / "runtime-identity.json"
    runtime_identity.write_text(json.dumps({"valid": True}))
    controls = tmp_path / "historical-controls.json"
    controls.write_text(
        json.dumps(
            {
                "writer_threads": {"value": 1, "provenance": "historically_proven"},
                "compression": {"value": "snappy", "provenance": "historically_proven"},
                "row_group_size": {"value": 122880, "provenance": "historically_proven"},
                "parquet_version": {"value": 1, "provenance": "historically_proven"},
                "string_dictionary_page_size_limit": {"value": 1048576, "provenance": "historically_proven"},
                "preserve_insertion_order": {"value": True, "provenance": "historically_proven"},
                "per_thread_output": {"value": False, "provenance": "historically_proven"},
                "write_partition_columns": {"value": False, "provenance": "historically_proven"},
                "append": {"value": False, "provenance": "historically_proven"},
                "overwrite": {"value": False, "provenance": "historically_proven"},
            }
        )
    )
    output = tmp_path / "contract"

    assert main(
        [
            "a0-contract",
            "--database", str(fixture.database_path),
            "--a0", str(fixture.parquet_path),
            "--runtime-identity", str(runtime_identity),
            "--historical-controls", str(controls),
            "--source-table", "synthetic_data",
            "--output", str(output),
        ]
    ) == 0

    contract = json.loads((output / "a0-contract.json").read_text())
    assert contract["valid"] is True
    assert contract["runtime_identity"]["sha256"]
    assert contract["source_table"] == "synthetic_data"
    assert contract["ducklake_involved"] is False
    assert json.loads(capsys.readouterr().out)["contract"].endswith("a0-contract.json")


def test_layout_timing_cli_refuses_any_pair_count_except_the_fixed_campaign_size(tmp_path) -> None:
    with pytest.raises(ValueError, match="exactly 19"):
        main(
            [
                "layout-noise",
                "--a0-contract", str(tmp_path / "contract.json"),
                "--output", str(tmp_path / "noise"),
                "--pairs", "18",
            ]
        )


def test_layout_timing_cli_refuses_to_label_local_development_as_gce_evidence(tmp_path) -> None:
    if cli_module.platform.system() == "Linux":
        pytest.skip("the development runner is already the permitted GCE operating system")
    with pytest.raises(RuntimeError, match="approved GCE Linux host"):
        main(
            [
                "layout-noise",
                "--a0-contract", str(tmp_path / "contract.json"),
                "--output", str(tmp_path / "noise"),
                "--pairs", "19",
            ]
        )


def test_layout_noise_and_screen_cli_write_fixed_block_evidence(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "_require_gce_local_execution", lambda: None)
    fixture = build_local_fixture(
        tmp_path / "fixture", SyntheticProfile(rows=500, seed=37, scenario=Scenario.SHUFFLED)
    )
    runtime_identity = tmp_path / "runtime-identity.json"
    runtime_identity.write_text(json.dumps({"valid": True}))
    controls = tmp_path / "historical-controls.json"
    controls.write_text(
        json.dumps(
            {
                name: {"value": 1, "provenance": "historically_proven"}
                for name in (
                    "writer_threads", "compression", "row_group_size", "parquet_version",
                    "string_dictionary_page_size_limit", "preserve_insertion_order", "per_thread_output",
                    "write_partition_columns", "append", "overwrite",
                )
            }
        )
    )
    contract_dir = tmp_path / "contract"
    assert main([
        "a0-contract", "--database", str(fixture.database_path), "--a0", str(fixture.parquet_path),
        "--runtime-identity", str(runtime_identity), "--historical-controls", str(controls),
        "--output", str(contract_dir),
    ]) == 0
    capsys.readouterr()
    contract = contract_dir / "a0-contract.json"
    noise_dir = tmp_path / "noise"
    assert main(["layout-noise", "--a0-contract", str(contract), "--output", str(noise_dir), "--pairs", "19"]) == 0
    noise = json.loads((noise_dir / "layout-noise-result.json").read_text())
    assert len(noise["screen"]["blocks"]) == 19
    assert len(noise["screen"]["observations"]) == 19 * 8
    assert len(noise["screen"]["q0"]) == 19

    layout_dir = tmp_path / "B1"
    assert main(["layout-generate", "--layout", "B1", "--a0-contract", str(contract), "--output", str(layout_dir)]) == 0
    capsys.readouterr()
    validation = layout_dir / "validation-result.json"
    assert main(["layout-validate", "--manifest", str(layout_dir / "layout-manifest.json"), "--output", str(validation)]) == 0
    capsys.readouterr()
    screen_dir = tmp_path / "screen"
    assert main([
        "layout-screen", "--layout-manifest", str(layout_dir / "layout-manifest.json"),
        "--validation", str(validation), "--noise-result", str(noise_dir / "layout-noise-result.json"),
        "--output", str(screen_dir), "--pairs", "19",
    ]) == 0
    screen = json.loads((screen_dir / "layout-screen-result.json").read_text())
    assert len(screen["screen"]["observations"]) == 19 * 8
    assert screen["ducklake_involved"] is False
    assert screen["data_provenance"] == "assumed_synthetic"


@pytest.mark.parametrize("remote_path", ["s3://bucket/a0.parquet", "gs://bucket/a0.parquet", "https://example.test/a0.parquet"])
def test_layout_contract_rejects_remote_data_uris_before_timing(tmp_path, remote_path) -> None:
    contract = tmp_path / "a0-contract.json"
    contract.write_text(
        json.dumps(
            {
                "valid": True,
                "source_paths": {"database": str(tmp_path / "benchmark.duckdb"), "a0": remote_path},
                "source": {"database": {}, "a0": {}},
                "runtime_identity": {"path": str(tmp_path / "runtime.json"), "sha256": "a" * 64},
            }
        )
    )

    with pytest.raises(ValueError, match="remote data URI"):
        cli_module._validated_a0_contract(contract)


def test_layout_generate_requires_an_identity_bound_a0_contract(tmp_path) -> None:
    fixture = build_local_fixture(
        tmp_path / "fixture", SyntheticProfile(rows=500, seed=43, scenario=Scenario.SHUFFLED)
    )
    contract = tmp_path / "a0-contract.json"
    contract.write_text(
        json.dumps(
            {
                "valid": True,
                "source_paths": {"database": str(fixture.database_path), "a0": str(fixture.parquet_path)},
                "source": {},
            }
        )
    )

    with pytest.raises(ValueError, match="missing database source identity"):
        main([
            "layout-generate", "--layout", "B1", "--a0-contract", str(contract),
            "--source-table", "synthetic_data", "--output", str(tmp_path / "B1"),
        ])


def test_verify_layout_harness_cli_checks_every_declared_member(tmp_path, capsys) -> None:
    root = tmp_path / "harness"
    root.mkdir()
    member = root / "src" / "module.py"
    member.parent.mkdir()
    member.write_text("value = 1\n")
    import hashlib

    manifest = tmp_path / "deployment-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "members": [
                    {
                        "path": "src/module.py",
                        "bytes": member.stat().st_size,
                        "sha256": hashlib.sha256(member.read_bytes()).hexdigest(),
                    }
                ]
            }
        )
    )

    assert main(["verify-layout-harness", "--manifest", str(manifest), "--root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
