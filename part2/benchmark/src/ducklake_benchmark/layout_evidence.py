"""Verification of immutable layout-result checksum chains before registration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .evidence import RunRecord, append_run, read_register


class EvidenceChainError(ValueError):
    pass


TERMINAL_STATUSES = {
    "validated", "unsupported", "invalid_correctness", "aborted_environment",
    "unstable", "valid_not_eligible", "eligible", "stable",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_link(result: dict[str, object], name: str) -> None:
    link = result.get(name)
    if not isinstance(link, dict) or not isinstance(link.get("path"), str) or not isinstance(link.get("sha256"), str):
        raise EvidenceChainError(f"missing {name} checksum link")
    path = Path(link["path"])
    if not path.is_file() or _sha256(path) != link["sha256"]:
        label = name.replace("_", " ").capitalize()
        raise EvidenceChainError(f"{label} checksum mismatch")


def verify_layout_result_chain(result_path: Path) -> dict[str, object]:
    """Re-read a terminal result and validate its declared local evidence links."""

    result = json.loads(result_path.read_text())
    if not isinstance(result, dict) or result.get("status") not in TERMINAL_STATUSES:
        raise EvidenceChainError("result has no recognised terminal status")
    if result_path.name == "layout-noise-result.json":
        _require_link(result, "a0_contract")
        screen = result.get("screen")
        if not isinstance(screen, dict) or len(screen.get("blocks", [])) != 19 or len(screen.get("observations", [])) != 152 or len(screen.get("q0", [])) != 19:
            raise EvidenceChainError("noise result does not contain a complete 19-block raw result")
    elif result_path.name == "layout-screen-result.json":
        for name in ("layout_manifest", "validation", "noise_result"):
            _require_link(result, name)
    return result


def register_layout_result(*, register_path: Path, workspace_root: Path, run_id: str, run_type: str, result_path: Path, artifact_paths: tuple[Path, ...]) -> dict[str, object]:
    result = verify_layout_result_chain(result_path)
    command_path = result_path.parent / "command.json"
    if not command_path.is_file():
        raise EvidenceChainError("terminal result is missing command.json")
    command = json.loads(command_path.read_text())
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        raise EvidenceChainError("command.json must be a JSON string array")
    timestamp = result.get("completed_at_utc")
    if not isinstance(timestamp, str):
        raise EvidenceChainError("terminal result is missing completed_at_utc")
    entry = append_run(register_path, workspace_root, RunRecord(run_id=run_id, recorded_at=timestamp, run_type=run_type, status=str(result["status"]), command=command, result_path=result_path, artifact_paths=artifact_paths, interpretation=f"Verified terminal layout status: {result['status']}."))
    read_register(register_path, workspace_root)
    return entry
