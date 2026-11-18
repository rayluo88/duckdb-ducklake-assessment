import hashlib
import json

import pytest

from ducklake_benchmark.layout_evidence import EvidenceChainError, verify_layout_result_chain
from ducklake_benchmark.cli import main


def test_layout_noise_chain_requires_current_a0_contract_hash(tmp_path) -> None:
    contract = tmp_path / "a0-contract.json"
    contract.write_text(json.dumps({"valid": True}))
    result = tmp_path / "layout-noise-result.json"
    result.write_text(
        json.dumps(
            {
                "status": "stable",
                "a0_contract": {"path": str(contract), "sha256": "0" * 64},
                "screen": {"blocks": list(range(1, 20)), "observations": [None] * 152, "q0": [None] * 19},
            }
        )
    )

    with pytest.raises(EvidenceChainError, match="A0 contract checksum"):
        verify_layout_result_chain(result)

    result_data = json.loads(result.read_text())
    result_data["a0_contract"]["sha256"] = hashlib.sha256(contract.read_bytes()).hexdigest()
    result.write_text(json.dumps(result_data))
    assert verify_layout_result_chain(result)["status"] == "stable"


def test_register_layout_evidence_reads_terminal_fields_from_verified_result(tmp_path, capsys) -> None:
    contract = tmp_path / "a0-contract.json"
    contract.write_text(json.dumps({"valid": True}))
    run = tmp_path / "noise"
    run.mkdir()
    (run / "command.json").write_text(json.dumps(["ducklake-benchmark", "layout-noise"]))
    result = run / "layout-noise-result.json"
    result.write_text(json.dumps({
        "status": "stable", "completed_at_utc": "2026-08-09T12:00:00Z",
        "scope": "gce_local_plain_parquet_layout_noise", "claim_boundary": "Noise only.",
        "a0_contract": {"path": str(contract), "sha256": hashlib.sha256(contract.read_bytes()).hexdigest()},
        "screen": {"blocks": list(range(1, 20)), "observations": [None] * 152, "q0": [None] * 19},
    }))
    register = tmp_path / "evidence" / "runs.jsonl"

    assert main(["register-layout-evidence", "--register", str(register), "--workspace-root", str(tmp_path), "--run-id", "P2-TEST-001", "--run-type", "layout_noise", "--result", str(result), "--artifact", str(contract)]) == 0
    entry = json.loads(register.read_text())
    assert entry["status"] == "stable"
    assert entry["recorded_at"] == "2026-08-09T12:00:00Z"
    assert entry["command"] == ["ducklake-benchmark", "layout-noise"]
    assert json.loads(capsys.readouterr().out)["sequence"] == 1


def test_register_layout_evidence_refuses_missing_immutable_completion_time(tmp_path) -> None:
    contract = tmp_path / "a0-contract.json"
    contract.write_text(json.dumps({"valid": True}))
    result = tmp_path / "layout-noise-result.json"
    (tmp_path / "command.json").write_text(json.dumps(["ducklake-benchmark", "layout-noise"]))
    result.write_text(json.dumps({"status": "stable", "a0_contract": {"path": str(contract), "sha256": hashlib.sha256(contract.read_bytes()).hexdigest()}, "screen": {"blocks": list(range(1, 20)), "observations": [None] * 152, "q0": [None] * 19}}))
    with pytest.raises(EvidenceChainError, match="completed_at_utc"):
        from ducklake_benchmark.layout_evidence import register_layout_result
        register_layout_result(register_path=tmp_path / "runs.jsonl", workspace_root=tmp_path, run_id="P2-TEST-002", run_type="layout_noise", result_path=result, artifact_paths=(contract,))
