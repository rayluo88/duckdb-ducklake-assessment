import json
import subprocess
import sys
import tarfile
from pathlib import Path


def test_create_layout_harness_archive_is_fresh_and_excludes_generated_material(tmp_path) -> None:
    benchmark_root = Path(__file__).resolve().parents[1]
    archive = tmp_path / "layout-harness.tar.gz"
    manifest = tmp_path / "layout-harness.manifest.json"

    subprocess.run(
        [
            sys.executable,
            "scripts/create_layout_harness_archive.py",
            "--output", str(archive),
            "--manifest", str(manifest),
        ],
        cwd=benchmark_root,
        check=True,
    )

    recorded = json.loads(manifest.read_text())
    assert recorded["archive"]["sha256"]
    assert {member["path"] for member in recorded["members"]} >= {
        "pyproject.toml", "uv.lock", "governing-design.sha256", "gcs-stage-design.sha256",
        "gcs-b1-stage-design.sha256", "gcs-ducklake-c1-stage-design.sha256",
        "schedule-fixture.json"
    }
    assert recorded["gcs_stage_design"]["sha256"]
    assert recorded["gcs_b1_stage_design"]["sha256"]
    assert recorded["gcs_ducklake_c1_stage_design"]["sha256"]
    with tarfile.open(archive, "r:gz") as bundle:
        names = set(bundle.getnames())
    assert "src/ducklake_benchmark/cli.py" in names
    assert not any(name.startswith((".venv/", ".pytest_cache/", "generated/")) for name in names)

    repeated = subprocess.run(
        [sys.executable, "scripts/create_layout_harness_archive.py", "--output", str(archive), "--manifest", str(manifest)],
        cwd=benchmark_root,
        capture_output=True,
        text=True,
    )
    assert repeated.returncode != 0
