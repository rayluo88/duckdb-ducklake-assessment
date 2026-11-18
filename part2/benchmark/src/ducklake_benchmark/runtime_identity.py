"""Fail-closed attestation of the pre-approved GCE benchmark runtime."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import duckdb


APPROVED_RUNTIME_ARCHIVE_SHA256 = "a2478d654ed51d443bae21ec20ad927f116b4f5aae4094ab74918a6aa38f0575"
APPROVED_DUCKDB_WHEEL_SHA256 = "fbf0f2d48b43c6c304d00463b463c27ead6c4b01c3c1816b750f728decf71afe"
APPROVED_PYTHON_VERSION = "3.14.7"
APPROVED_DUCKDB_VERSION = "1.5.5"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(path)
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def _vendor_inventory(vendor_root: Path) -> list[dict[str, object]]:
    if not vendor_root.is_dir() or vendor_root.is_symlink():
        raise FileNotFoundError(vendor_root)
    files: list[dict[str, object]] = []
    for path in sorted(vendor_root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"vendor inventory cannot contain symlinks: {path}")
        if path.is_file():
            identity = _identity(path)
            files.append(
                {
                    "path": path.relative_to(vendor_root).as_posix(),
                    "bytes": identity["bytes"],
                    "sha256": identity["sha256"],
                }
            )
    if not files:
        raise ValueError("vendor root must contain at least one regular file")
    return files


def _runtime_versions(python_path: Path) -> dict[str, str]:
    command = [
        str(python_path),
        "-c",
        "import json, sys, duckdb; print(json.dumps({'python_version': sys.version.split()[0], 'duckdb_version': duckdb.__version__}))",
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict) or not all(isinstance(payload.get(key), str) for key in ("python_version", "duckdb_version")):
        raise ValueError("interpreter version probe produced an invalid result")
    return {"python_version": payload["python_version"], "duckdb_version": payload["duckdb_version"]}


def _baseline_contains_identity(value: Any, digest: str) -> bool:
    if isinstance(value, dict):
        return any(_baseline_contains_identity(item, digest) for item in value.values())
    if isinstance(value, list):
        return any(_baseline_contains_identity(item, digest) for item in value)
    return value == digest


def attest_runtime(
    *,
    baseline_deployment_manifest: Path,
    runtime_archive: Path,
    duckdb_wheel: Path,
    python_path: Path,
    vendor_root: Path,
) -> dict[str, object]:
    """Verify all immutable runtime identities and return an attestable manifest."""

    baseline = json.loads(baseline_deployment_manifest.read_text())
    if not isinstance(baseline, dict):
        raise ValueError("baseline deployment manifest must be a JSON object")
    archive = _identity(runtime_archive)
    wheel = _identity(duckdb_wheel)
    if archive["sha256"] != APPROVED_RUNTIME_ARCHIVE_SHA256:
        raise ValueError("runtime archive SHA-256 differs from the approved artifact")
    if wheel["sha256"] != APPROVED_DUCKDB_WHEEL_SHA256:
        raise ValueError("DuckDB wheel SHA-256 differs from the approved artifact")
    if not _baseline_contains_identity(baseline, archive["sha256"]):
        raise ValueError("baseline deployment manifest does not bind the runtime archive")
    if not _baseline_contains_identity(baseline, wheel["sha256"]):
        raise ValueError("baseline deployment manifest does not bind the DuckDB wheel")
    # Virtual-environment interpreter launchers are normally symlinks; bind the resolved binary.
    python = _identity(python_path.resolve())
    versions = _runtime_versions(python_path)
    if versions["python_version"] != APPROVED_PYTHON_VERSION:
        raise ValueError("active Python version differs from the approved runtime")
    if versions["duckdb_version"] != APPROVED_DUCKDB_VERSION:
        raise ValueError("active DuckDB version differs from the approved runtime")
    return {
        "manifest_version": 1,
        "valid": True,
        "baseline_deployment_manifest": _identity(baseline_deployment_manifest),
        "runtime_archive": archive,
        "duckdb_wheel": wheel,
        "python": {**python, "version": versions["python_version"]},
        "duckdb_version": versions["duckdb_version"],
        "vendor": {"root": str(vendor_root.resolve()), "files": _vendor_inventory(vendor_root)},
    }
