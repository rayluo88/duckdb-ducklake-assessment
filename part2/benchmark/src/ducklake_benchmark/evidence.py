import csv
import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path


class EvidenceIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    recorded_at: str
    run_type: str
    status: str
    command: list[str]
    result_path: Path
    artifact_paths: tuple[Path, ...]
    interpretation: str


@dataclass(frozen=True)
class CompiledOutputs:
    markdown_path: Path
    csv_path: Path


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry_sha256(entry: dict[str, object]) -> str:
    unsigned = {key: value for key, value in entry.items() if key != "entry_sha256"}
    payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    return sha256(payload).hexdigest()


def _relative_file(path: Path, workspace_root: Path) -> tuple[Path, str]:
    resolved_root = workspace_root.resolve()
    resolved_path = path.resolve()
    try:
        relative_path = resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"evidence path must remain inside workspace: {path}") from error
    if not resolved_path.is_file():
        raise FileNotFoundError(path)
    return resolved_path, relative_path.as_posix()


def _validate_timestamp(value: str) -> None:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("recorded_at must include a timezone")


def append_run(register_path: Path, workspace_root: Path, record: RunRecord) -> dict[str, object]:
    _validate_timestamp(record.recorded_at)
    existing = read_register(register_path, workspace_root) if register_path.exists() else []
    if any(entry["run_id"] == record.run_id for entry in existing):
        raise ValueError(f"duplicate run_id: {record.run_id}")

    result_file, result_relative = _relative_file(record.result_path, workspace_root)
    result = json.loads(result_file.read_text())
    if not isinstance(result, dict):
        raise ValueError("result JSON must contain an object")

    artifacts = []
    for path in record.artifact_paths:
        artifact_file, artifact_relative = _relative_file(path, workspace_root)
        artifacts.append(
            {
                "path": artifact_relative,
                "bytes": artifact_file.stat().st_size,
                "sha256": _sha256_file(artifact_file),
            }
        )

    entry: dict[str, object] = {
        "register_version": 1,
        "sequence": len(existing) + 1,
        "run_id": record.run_id,
        "recorded_at": record.recorded_at,
        "run_type": record.run_type,
        "status": record.status,
        "command": record.command,
        "result_path": result_relative,
        "result_bytes": result_file.stat().st_size,
        "result_sha256": _sha256_file(result_file),
        "artifacts": artifacts,
        "scope": result.get("scope", ""),
        "claim_boundary": result.get("claim_boundary", ""),
        "environment": result.get("environment", {}),
        "interpretation": record.interpretation,
        "previous_entry_sha256": existing[-1]["entry_sha256"] if existing else None,
    }
    entry["entry_sha256"] = _entry_sha256(entry)
    register_path.parent.mkdir(parents=True, exist_ok=True)
    with register_path.open("a") as handle:
        handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
    return entry


def read_register(register_path: Path, workspace_root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    previous_hash = None
    for line_number, line in enumerate(register_path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("sequence") != len(entries) + 1:
            raise EvidenceIntegrityError(f"invalid sequence at line {line_number}")
        if entry.get("previous_entry_sha256") != previous_hash:
            raise EvidenceIntegrityError(f"broken hash chain at line {line_number}")
        if entry.get("entry_sha256") != _entry_sha256(entry):
            raise EvidenceIntegrityError(f"entry hash mismatch at line {line_number}")

        result_file, _ = _relative_file(workspace_root / entry["result_path"], workspace_root)
        if _sha256_file(result_file) != entry.get("result_sha256"):
            raise EvidenceIntegrityError(f"result checksum mismatch for {entry.get('run_id')}")
        for artifact in entry.get("artifacts", []):
            artifact_file, _ = _relative_file(workspace_root / artifact["path"], workspace_root)
            if _sha256_file(artifact_file) != artifact.get("sha256"):
                raise EvidenceIntegrityError(
                    f"artifact checksum mismatch for {entry.get('run_id')}: {artifact['path']}"
                )
        entries.append(entry)
        previous_hash = entry["entry_sha256"]
    return entries


def _markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def compile_run_tables(
    register_path: Path, workspace_root: Path, output_dir: Path
) -> CompiledOutputs:
    if output_dir.exists():
        raise FileExistsError(f"compiled output already exists: {output_dir}")
    entries = read_register(register_path, workspace_root)
    output_dir.mkdir(parents=True)
    columns = [
        "sequence",
        "recorded_at",
        "run_id",
        "run_type",
        "status",
        "scope",
        "duckdb_version",
        "result_path",
        "artifact_count",
        "integrity",
        "interpretation",
        "claim_boundary",
    ]
    rows = []
    for entry in entries:
        environment = entry.get("environment", {})
        rows.append(
            {
                "sequence": entry["sequence"],
                "recorded_at": entry["recorded_at"],
                "run_id": entry["run_id"],
                "run_type": entry["run_type"],
                "status": entry["status"],
                "scope": entry["scope"],
                "duckdb_version": environment.get("duckdb_version", ""),
                "result_path": entry["result_path"],
                "artifact_count": len(entry["artifacts"]) + 1,
                "integrity": "verified",
                "interpretation": entry["interpretation"],
                "claim_boundary": entry["claim_boundary"],
            }
        )

    csv_path = output_dir / "campaign-runs.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    markdown_path = output_dir / "campaign-runs.md"
    visible_columns = ["run_id", "run_type", "status", "scope", "interpretation"]
    markdown_lines = [
        "# DuckLake Part 2 Campaign Run Register",
        "",
        "Derived from the verified append-only JSONL register. Raw results remain authoritative.",
        "",
        "| Run ID | Type | Status | Scope | Interpretation |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        markdown_lines.append(
            "| " + " | ".join(_markdown_cell(row[column]) for column in visible_columns) + " |"
        )
    markdown_path.write_text("\n".join(markdown_lines) + "\n")
    return CompiledOutputs(markdown_path=markdown_path, csv_path=csv_path)
