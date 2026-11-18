from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def test_installed_entrypoint_runs_without_source_path_injection(tmp_path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    command = Path(sys.executable).parent / "ducklake-s3-preflight"
    result = subprocess.run(
        [
            command,
            "--config",
            project_root / "config/local-smoke.json",
            "--output",
            tmp_path / "preflight.json",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "preflight passed" in result.stdout
