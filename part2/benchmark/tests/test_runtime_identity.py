import json
import sys
from pathlib import Path

from ducklake_benchmark import runtime_identity


def test_attest_runtime_binds_exact_runtime_files_and_vendor_inventory(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "runtime.tar.gz"
    wheel = tmp_path / "duckdb.whl"
    archive.write_bytes(b"runtime-archive")
    wheel.write_bytes(b"duckdb-wheel")
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "duckdb.py").write_text("# vendor fixture\n")
    monkeypatch.setattr(runtime_identity, "APPROVED_RUNTIME_ARCHIVE_SHA256", runtime_identity.sha256_file(archive))
    monkeypatch.setattr(runtime_identity, "APPROVED_DUCKDB_WHEEL_SHA256", runtime_identity.sha256_file(wheel))
    monkeypatch.setattr(runtime_identity, "APPROVED_PYTHON_VERSION", ".".join(map(str, sys.version_info[:3])))
    monkeypatch.setattr(runtime_identity, "APPROVED_DUCKDB_VERSION", runtime_identity.duckdb.__version__)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"runtime_archive_sha256": runtime_identity.sha256_file(archive), "duckdb_wheel_sha256": runtime_identity.sha256_file(wheel)}))

    result = runtime_identity.attest_runtime(
        baseline_deployment_manifest=baseline,
        runtime_archive=archive,
        duckdb_wheel=wheel,
        python_path=Path(sys.executable),
        vendor_root=vendor,
    )

    assert result["valid"] is True
    assert result["vendor"]["files"][0]["path"] == "duckdb.py"
    assert result["baseline_deployment_manifest"]["sha256"]
