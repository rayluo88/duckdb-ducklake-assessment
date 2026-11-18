import csv
import json

import pytest

from ducklake_benchmark.evidence import (
    EvidenceIntegrityError,
    RunRecord,
    append_run,
    compile_run_tables,
    read_register,
)


def _run_record(result_path, artifact_path, run_id="P2-TEST-001") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        recorded_at="2026-08-09T12:00:00+08:00",
        run_type="calibration",
        status="completed_with_unstable_cells",
        command=["ducklake-benchmark", "calibrate-local", "--rows", "1000"],
        result_path=result_path,
        artifact_paths=(artifact_path,),
        interpretation="Method passed; performance claim withheld.",
    )


def test_append_run_records_relative_artifacts_and_a_hash_chain(tmp_path) -> None:
    result = tmp_path / "run-1" / "result.json"
    result.parent.mkdir()
    result.write_text(
        json.dumps(
            {
                "scope": "local_a0_vs_a0_noise_calibration",
                "claim_boundary": "Local evidence only.",
                "environment": {"duckdb_version": "1.5.5"},
            }
        )
    )
    artifact = result.parent / "data.parquet"
    artifact.write_bytes(b"parquet-evidence")
    register = tmp_path / "evidence" / "run-register.jsonl"

    first = append_run(register, tmp_path, _run_record(result, artifact))
    second = append_run(
        register,
        tmp_path,
        _run_record(result, artifact, run_id="P2-TEST-002"),
    )

    assert first["sequence"] == 1
    assert first["previous_entry_sha256"] is None
    assert len(first["entry_sha256"]) == 64
    assert first["result_path"] == "run-1/result.json"
    assert first["artifacts"][0]["path"] == "run-1/data.parquet"
    assert first["scope"] == "local_a0_vs_a0_noise_calibration"
    assert first["environment"]["duckdb_version"] == "1.5.5"
    assert second["sequence"] == 2
    assert second["previous_entry_sha256"] == first["entry_sha256"]
    assert read_register(register, tmp_path) == [first, second]


def test_append_run_rejects_duplicate_ids_and_paths_outside_workspace(tmp_path) -> None:
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"scope": "test", "claim_boundary": "test"}))
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"evidence")
    register = tmp_path / "run-register.jsonl"
    record = _run_record(result, artifact)
    append_run(register, tmp_path, record)

    with pytest.raises(ValueError, match="duplicate run_id"):
        append_run(register, tmp_path, record)

    outside = tmp_path.parent / "outside-evidence.bin"
    outside.write_bytes(b"outside")
    with pytest.raises(ValueError, match="workspace"):
        append_run(
            register,
            tmp_path,
            RunRecord(**{**record.__dict__, "run_id": "P2-TEST-002", "artifact_paths": (outside,)}),
        )


def test_read_register_detects_edited_entries_and_artifacts(tmp_path) -> None:
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"scope": "test", "claim_boundary": "test"}))
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"original")
    register = tmp_path / "run-register.jsonl"
    append_run(register, tmp_path, _run_record(result, artifact))

    artifact.write_bytes(b"changed")
    with pytest.raises(EvidenceIntegrityError, match="artifact checksum"):
        read_register(register, tmp_path)

    artifact.write_bytes(b"original")
    entry = json.loads(register.read_text())
    entry["status"] = "passed"
    register.write_text(json.dumps(entry) + "\n")
    with pytest.raises(EvidenceIntegrityError, match="entry hash"):
        read_register(register, tmp_path)


def test_compile_run_tables_produces_deterministic_markdown_and_csv(tmp_path) -> None:
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
    register = tmp_path / "run-register.jsonl"
    append_run(register, tmp_path, _run_record(result, artifact))

    outputs = compile_run_tables(register, tmp_path, tmp_path / "compiled")

    markdown = outputs.markdown_path.read_text()
    assert "| P2-TEST-001 | calibration | completed_with_unstable_cells |" in markdown
    assert "Method passed; performance claim withheld." in markdown
    with outputs.csv_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["run_id"] == "P2-TEST-001"
    assert rows[0]["duckdb_version"] == "1.5.5"
    assert rows[0]["integrity"] == "verified"

    with pytest.raises(FileExistsError):
        compile_run_tables(register, tmp_path, tmp_path / "compiled")
