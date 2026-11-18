"""Create a fresh, minimal deployment archive for the local layout harness."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path


def _identity(path: Path) -> dict[str, object]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"bytes": path.stat().st_size, "sha256": digest}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink() or args.manifest.exists() or args.manifest.is_symlink():
        raise FileExistsError("archive and deployment manifest outputs must both be fresh")

    root = Path(__file__).resolve().parents[1]
    part2_root = root.parent
    schedule = root / "tests" / "fixtures" / "layout_screen_schedule.json"
    design = part2_root / "planning" / "layout-screen-design.md"
    gcs_stage_design = part2_root / "stages" / "gcs-bstar-stage.md"
    gcs_b1_stage_design = part2_root / "stages" / "gcs-b1-stage.md"
    gcs_ducklake_c1_stage_design = part2_root / "stages" / "gcs-ducklake-c1-stage.md"
    required = [
        root / "pyproject.toml", root / "uv.lock", schedule, design, gcs_stage_design,
        gcs_b1_stage_design, gcs_ducklake_c1_stage_design,
    ]
    if any(not path.is_file() for path in required):
        raise FileNotFoundError("required harness source is missing")
    members = [(path.relative_to(root).as_posix(), path) for path in sorted((root / "src").rglob("*.py"))]
    members.extend([("pyproject.toml", root / "pyproject.toml"), ("uv.lock", root / "uv.lock"), ("schedule-fixture.json", schedule)])
    design_digest = hashlib.sha256(design.read_bytes()).hexdigest()
    gcs_stage_design_digest = hashlib.sha256(gcs_stage_design.read_bytes()).hexdigest()
    gcs_b1_stage_design_digest = hashlib.sha256(gcs_b1_stage_design.read_bytes()).hexdigest()
    gcs_ducklake_c1_stage_design_digest = hashlib.sha256(
        gcs_ducklake_c1_stage_design.read_bytes()
    ).hexdigest()
    generated = design_digest.encode() + b"\n"
    gcs_stage_generated = gcs_stage_design_digest.encode() + b"\n"
    gcs_b1_stage_generated = gcs_b1_stage_design_digest.encode() + b"\n"
    gcs_ducklake_c1_stage_generated = gcs_ducklake_c1_stage_design_digest.encode() + b"\n"
    manifest_members = [{"path": name, **_identity(path)} for name, path in members]
    manifest_members.append({"path": "governing-design.sha256", "bytes": len(generated), "sha256": hashlib.sha256(generated).hexdigest()})
    manifest_members.append({"path": "gcs-stage-design.sha256", "bytes": len(gcs_stage_generated), "sha256": hashlib.sha256(gcs_stage_generated).hexdigest()})
    manifest_members.append({"path": "gcs-b1-stage-design.sha256", "bytes": len(gcs_b1_stage_generated), "sha256": hashlib.sha256(gcs_b1_stage_generated).hexdigest()})
    manifest_members.append({"path": "gcs-ducklake-c1-stage-design.sha256", "bytes": len(gcs_ducklake_c1_stage_generated), "sha256": hashlib.sha256(gcs_ducklake_c1_stage_generated).hexdigest()})

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(args.output, "w:gz") as archive:
        for name, path in members:
            archive.add(path, arcname=name, recursive=False)
        info = tarfile.TarInfo("governing-design.sha256")
        info.size = len(generated)
        archive.addfile(info, io.BytesIO(generated))
        gcs_info = tarfile.TarInfo("gcs-stage-design.sha256")
        gcs_info.size = len(gcs_stage_generated)
        archive.addfile(gcs_info, io.BytesIO(gcs_stage_generated))
        gcs_b1_info = tarfile.TarInfo("gcs-b1-stage-design.sha256")
        gcs_b1_info.size = len(gcs_b1_stage_generated)
        archive.addfile(gcs_b1_info, io.BytesIO(gcs_b1_stage_generated))
        gcs_ducklake_c1_info = tarfile.TarInfo("gcs-ducklake-c1-stage-design.sha256")
        gcs_ducklake_c1_info.size = len(gcs_ducklake_c1_stage_generated)
        archive.addfile(gcs_ducklake_c1_info, io.BytesIO(gcs_ducklake_c1_stage_generated))
    manifest = {
        "manifest_version": 1,
        "archive": {"path": str(args.output.resolve()), **_identity(args.output)},
        "governing_design": {"path": str(design.resolve()), "sha256": design_digest},
        "gcs_stage_design": {
            "path": str(gcs_stage_design.resolve()),
            "sha256": gcs_stage_design_digest,
        },
        "gcs_b1_stage_design": {
            "path": str(gcs_b1_stage_design.resolve()),
            "sha256": gcs_b1_stage_design_digest,
        },
        "gcs_ducklake_c1_stage_design": {
            "path": str(gcs_ducklake_c1_stage_design.resolve()),
            "sha256": gcs_ducklake_c1_stage_design_digest,
        },
        "members": manifest_members,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"archive": str(args.output), "manifest": str(args.manifest)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
