import argparse
import json
import platform
import shutil
import sys
from hashlib import sha256
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from .calibration import (
    CalibrationConfig,
    RemoteCalibrationLanes,
    calibrate_parquet_pair,
    calibrate_parquet_uris,
    prepare_identical_lanes,
)
from .a0_contract import HistoricalControl, REQUIRED_WRITER_CONTROLS, write_a0_contract
from .correctness import validate_relations
from .evidence import RunRecord, append_run, compile_run_tables, read_register
from .fixture import build_local_fixture
from .gcp_guardrails import GuardrailConfig, verify_guardrail_bundle, write_guardrail_bundle
from .gcs import (
    configure_duckdb_gcs_oauth,
    gcs_media_url,
    read_gcs_manifest,
    read_gcs_screen_manifest,
    render_gcs_read_parquet,
)
from .ducklake_c1 import (
    bootstrap_ducklake_c1,
    configure_ducklake_c1_connection,
    read_ducklake_c1_manifest,
    validate_ducklake_c1,
)
from .layout_correctness import validate_layout
from .layouts import generate_layout, layout_specs
from .layout_screen import (
    Q0Observation,
    ScreenObservation,
    evaluate_eligibility,
    run_paired_relation_screen,
    run_paired_screen,
    summarise_noise,
)
from .runtime_identity import attest_runtime
from .layout_evidence import register_layout_result
from .model import Scenario, SyntheticProfile
from .provenance import hash_tree
from .queries import query_specs, run_query_suite


CALIBRATION_ENVIRONMENTS = {
    "local_mac": {
        "scope": "local_a0_vs_a0_noise_calibration",
        "execution_environment": {"kind": "local_mac"},
        "claim_boundary": (
            "Local Mac A0-versus-A0 timing-noise evidence only; no GCP, production data, S3, "
            "layout-improvement, or DuckLake claim."
        ),
    },
    "gce_local": {
        "scope": "gce_local_a0_vs_a0_noise_calibration",
        "execution_environment": {
            "kind": "gce_local",
            "project_id": "ducklake-example-project",
            "zone": "asia-southeast1-b",
            "vm_name": "ducklake-p2-bench-01",
            "storage": "boot_pd_balanced",
        },
        "claim_boundary": (
            "GCE-local-storage A0-versus-A0 timing-noise evidence only; no GCS, production data, "
            "S3, layout-improvement, or DuckLake claim."
        ),
    },
}


def _file_identity(path: Path) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(path)
    return {"bytes": path.stat().st_size, "sha256": sha256(path.read_bytes()).hexdigest()}


def _json_object(path: Path, *, label: str) -> dict[str, object]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _reject_remote_or_credential_values(value: object) -> None:
    """Reject remote data locations and credential-shaped configuration recursively."""

    if isinstance(value, dict):
        for key, nested in value.items():
            normalised_key = str(key).lower().replace("_", "-")
            if any(marker in normalised_key for marker in ("credential", "secret", "access-key", "token", "password")):
                raise ValueError("credential-shaped values are outside the local layout scope")
            _reject_remote_or_credential_values(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_remote_or_credential_values(nested)
    elif isinstance(value, str) and value.lower().startswith(("gs://", "s3://", "http://", "https://")):
        raise ValueError("remote data URI is outside the local layout scope")


def _reject_remote_extensions(contract: dict[str, object]) -> None:
    environment = contract.get("environment")
    extensions = environment.get("duckdb_extensions") if isinstance(environment, dict) else None
    if not isinstance(extensions, list):
        return
    for extension in extensions:
        if not isinstance(extension, dict):
            continue
        name = str(extension.get("extension_name", "")).lower()
        if name in {"httpfs", "ducklake"} and extension.get("loaded") is True:
            raise ValueError(f"loaded {name} extension is outside the local layout scope")


def _validated_a0_contract(path: Path) -> dict[str, object]:
    contract = _json_object(path, label="a0-contract")
    if contract.get("valid") is not True:
        raise ValueError("layout timing requires a valid A0 contract")
    _reject_remote_or_credential_values(contract)
    _reject_remote_extensions(contract)
    source_paths = contract.get("source_paths")
    source = contract.get("source")
    if not isinstance(source_paths, dict) or not isinstance(source, dict):
        raise ValueError("a0-contract must include source paths and identities")
    for name in ("database", "a0"):
        raw_path, expected = source_paths.get(name), source.get(name)
        if not isinstance(raw_path, str) or not isinstance(expected, dict):
            raise ValueError(f"a0-contract missing {name} source identity")
        if _file_identity(Path(raw_path).resolve()) != expected:
            raise ValueError(f"a0-contract {name} source identity drift")
    runtime = contract.get("runtime_identity")
    if not isinstance(runtime, dict) or not isinstance(runtime.get("path"), str) or not isinstance(runtime.get("sha256"), str):
        raise ValueError("a0-contract must bind a runtime-identity manifest")
    runtime_path = Path(runtime["path"])
    if not runtime_path.is_file() or sha256(runtime_path.read_bytes()).hexdigest() != runtime["sha256"]:
        raise ValueError("a0-contract runtime-identity drift")
    if _json_object(runtime_path, label="runtime-identity").get("valid") is not True:
        raise ValueError("a0-contract runtime identity is not valid")
    return contract


def _require_gce_local_execution() -> None:
    """Prevent a local-development run from being labelled as GCE-local evidence."""

    if platform.system() != "Linux":
        raise RuntimeError("layout timing commands may run only on the approved GCE Linux host")


def main(argv: list[str] | None = None) -> int:
    recorded_argv = ["ducklake-benchmark", *(argv if argv is not None else sys.argv[1:])]

    def write_command_record(directory: Path, label: str) -> Path:
        """Preserve command provenance without overwriting an earlier command record."""

        directory.mkdir(parents=True, exist_ok=True)
        primary = directory / "command.json"
        target = primary if not primary.exists() else directory / f"command-{label}.json"
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"command record already exists: {target}")
        target.write_text(json.dumps(recorded_argv, indent=2) + "\n")
        return target

    parser = argparse.ArgumentParser(prog="ducklake-benchmark")
    subparsers = parser.add_subparsers(dest="command", required=True)
    smoke_parser = subparsers.add_parser("smoke", help="run the local synthetic functional gate")
    smoke_parser.add_argument("--output", required=True, type=Path)
    smoke_parser.add_argument("--rows", default=10_000, type=int)
    smoke_parser.add_argument("--seed", default=20260809, type=int)
    smoke_parser.add_argument(
        "--scenario",
        choices=[scenario.value for scenario in Scenario],
        default=Scenario.SHUFFLED.value,
    )
    smoke_parser.add_argument(
        "--part1-reference",
        type=Path,
        default=Path(__file__).resolve().parents[4] / "part1" / "poc",
    )
    calibration_parser = subparsers.add_parser(
        "calibrate-local", help="measure local A0-versus-A0 Parquet timing noise"
    )
    calibration_parser.add_argument("--output", required=True, type=Path)
    calibration_parser.add_argument(
        "--environment-scope",
        required=True,
        choices=sorted(CALIBRATION_ENVIRONMENTS),
    )
    calibration_parser.add_argument("--rows", default=10_000, type=int)
    calibration_parser.add_argument("--seed", default=20260809, type=int)
    calibration_parser.add_argument(
        "--scenario",
        choices=[scenario.value for scenario in Scenario],
        default=Scenario.SHUFFLED.value,
    )
    calibration_parser.add_argument("--warmups", default=2, type=int)
    calibration_parser.add_argument("--initial-pairs", default=7, type=int)
    calibration_parser.add_argument("--pair-increment", default=4, type=int)
    calibration_parser.add_argument("--max-pairs", default=19, type=int)
    calibration_parser.add_argument("--bootstrap-samples", default=2_000, type=int)
    calibration_parser.add_argument("--precision-ratio", default=1.05, type=float)
    gcs_parser = subparsers.add_parser(
        "calibrate-gcs", help="measure same-region GCS A0-versus-A0 Parquet timing noise"
    )
    gcs_parser.add_argument("--output", required=True, type=Path)
    gcs_parser.add_argument("--source-manifest", required=True, type=Path)
    gcs_parser.add_argument("--httpfs-extension", required=True, type=Path)
    gcs_parser.add_argument("--seed", default=20260809, type=int)
    gcs_parser.add_argument("--warmups", default=2, type=int)
    gcs_parser.add_argument("--initial-pairs", default=7, type=int)
    gcs_parser.add_argument("--pair-increment", default=4, type=int)
    gcs_parser.add_argument("--max-pairs", default=19, type=int)
    gcs_parser.add_argument("--bootstrap-samples", default=2_000, type=int)
    gcs_parser.add_argument("--precision-ratio", default=1.05, type=float)
    gcs_screen_parser = subparsers.add_parser(
        "gcs-screen", help="run the fixed same-region GCS A0/A0 control or approved layout screen"
    )
    gcs_screen_parser.add_argument("--mode", required=True, choices=("noise", "screen"))
    gcs_screen_parser.add_argument("--output", required=True, type=Path)
    gcs_screen_parser.add_argument("--stage-manifest", required=True, type=Path)
    gcs_screen_parser.add_argument("--httpfs-extension", required=True, type=Path)
    gcs_screen_parser.add_argument("--noise-result", type=Path)
    ducklake_screen_parser = subparsers.add_parser(
        "ducklake-screen",
        help="run the controlled DuckLake C1 catalogue-over-identical-Parquet comparison",
    )
    ducklake_screen_parser.add_argument("--mode", required=True, choices=("noise", "screen"))
    ducklake_screen_parser.add_argument("--output", required=True, type=Path)
    ducklake_screen_parser.add_argument("--stage-manifest", required=True, type=Path)
    ducklake_screen_parser.add_argument("--httpfs-extension", required=True, type=Path)
    ducklake_screen_parser.add_argument("--ducklake-extension", required=True, type=Path)
    ducklake_screen_parser.add_argument("--noise-result", type=Path)
    ducklake_screen_parser.add_argument("--validation", type=Path)
    ducklake_screen_parser.add_argument(
        "--comparison",
        choices=("a0_representation", "direct_clone_increment"),
        default="a0_representation",
        help=(
            "a0_representation preserves the broader historical comparison; "
            "direct_clone_increment compares DuckLake with a direct read of the same C1 object"
        ),
    )
    ducklake_bootstrap_parser = subparsers.add_parser(
        "ducklake-bootstrap-c1",
        help="create and validate the local C1 DuckLake catalogue over one immutable clone",
    )
    ducklake_bootstrap_parser.add_argument("--plan", required=True, type=Path)
    ducklake_bootstrap_parser.add_argument("--output", required=True, type=Path)
    ducklake_bootstrap_parser.add_argument("--httpfs-extension", required=True, type=Path)
    ducklake_bootstrap_parser.add_argument("--ducklake-extension", required=True, type=Path)
    ducklake_validate_parser = subparsers.add_parser(
        "ducklake-validate-c1",
        help="run untimed equivalence and physical-order gates for the C1 catalogue",
    )
    ducklake_validate_parser.add_argument("--stage-manifest", required=True, type=Path)
    ducklake_validate_parser.add_argument("--output", required=True, type=Path)
    ducklake_validate_parser.add_argument("--httpfs-extension", required=True, type=Path)
    ducklake_validate_parser.add_argument("--ducklake-extension", required=True, type=Path)
    ducklake_validate_parser.add_argument(
        "--comparison",
        choices=("a0_representation", "direct_clone_increment"),
        default="a0_representation",
    )
    layout_generate_parser = subparsers.add_parser(
        "layout-generate", help="write one frozen local plain-Parquet layout"
    )
    layout_generate_parser.add_argument("--layout", required=True, choices=[spec.layout_id for spec in layout_specs()])
    layout_generate_parser.add_argument("--a0-contract", required=True, type=Path)
    layout_generate_parser.add_argument("--source-table")
    layout_generate_parser.add_argument("--output", required=True, type=Path)
    layout_validate_parser = subparsers.add_parser(
        "layout-validate", help="run untimed correctness and physical-layout gates"
    )
    layout_validate_parser.add_argument("--manifest", required=True, type=Path)
    layout_validate_parser.add_argument("--output", required=True, type=Path)
    layout_noise_parser = subparsers.add_parser(
        "layout-noise", help="measure the fixed 19-block local A0-versus-A0 control"
    )
    layout_noise_parser.add_argument("--a0-contract", required=True, type=Path)
    layout_noise_parser.add_argument("--output", required=True, type=Path)
    layout_noise_parser.add_argument("--pairs", required=True, type=int)
    layout_noise_parser.add_argument("--seed", default=20260809, type=int)
    layout_screen_parser = subparsers.add_parser(
        "layout-screen", help="screen one validated layout against the fixed local A0 control"
    )
    layout_screen_parser.add_argument("--layout-manifest", required=True, type=Path)
    layout_screen_parser.add_argument("--validation", required=True, type=Path)
    layout_screen_parser.add_argument("--noise-result", required=True, type=Path)
    layout_screen_parser.add_argument("--output", required=True, type=Path)
    layout_screen_parser.add_argument("--pairs", required=True, type=int)
    layout_screen_parser.add_argument("--seed", default=20260809, type=int)
    a0_contract_parser = subparsers.add_parser(
        "a0-contract", help="inventory immutable A0 and freeze its writer-control provenance"
    )
    a0_contract_parser.add_argument("--database", required=True, type=Path)
    a0_contract_parser.add_argument("--a0", required=True, type=Path)
    a0_contract_parser.add_argument("--runtime-identity", required=True, type=Path)
    a0_contract_parser.add_argument("--historical-controls", type=Path)
    a0_contract_parser.add_argument("--source-table", default="synthetic_data")
    a0_contract_parser.add_argument("--output", required=True, type=Path)
    runtime_identity_parser = subparsers.add_parser(
        "runtime-identity", help="attest the frozen GCE Python, DuckDB and vendor runtime"
    )
    runtime_identity_parser.add_argument("--baseline-deployment-manifest", required=True, type=Path)
    runtime_identity_parser.add_argument("--runtime-archive", required=True, type=Path)
    runtime_identity_parser.add_argument("--duckdb-wheel", required=True, type=Path)
    runtime_identity_parser.add_argument("--python", required=True, type=Path)
    runtime_identity_parser.add_argument("--vendor", required=True, type=Path)
    runtime_identity_parser.add_argument("--output", required=True, type=Path)
    verify_harness_parser = subparsers.add_parser(
        "verify-layout-harness", help="verify every file declared in a layout harness deployment manifest"
    )
    verify_harness_parser.add_argument("--manifest", required=True, type=Path)
    verify_harness_parser.add_argument("--root", required=True, type=Path)
    register_parser = subparsers.add_parser(
        "register-evidence", help="append one verified result to the campaign register"
    )
    register_parser.add_argument("--register", required=True, type=Path)
    register_parser.add_argument("--workspace-root", required=True, type=Path)
    register_parser.add_argument("--run-id", required=True)
    register_parser.add_argument("--recorded-at", required=True)
    register_parser.add_argument("--run-type", required=True)
    register_parser.add_argument("--status", required=True)
    register_parser.add_argument("--result", required=True, type=Path)
    register_parser.add_argument("--artifact", action="append", default=[], type=Path)
    register_parser.add_argument("--interpretation", required=True)
    register_parser.add_argument("--command-json", required=True)
    layout_register_parser = subparsers.add_parser("register-layout-evidence", help="append a checksum-verified terminal layout result")
    layout_register_parser.add_argument("--register", required=True, type=Path)
    layout_register_parser.add_argument("--workspace-root", required=True, type=Path)
    layout_register_parser.add_argument("--run-id", required=True)
    layout_register_parser.add_argument("--run-type", required=True)
    layout_register_parser.add_argument("--result", required=True, type=Path)
    layout_register_parser.add_argument("--artifact", action="append", default=[], type=Path)
    compile_parser = subparsers.add_parser(
        "compile-evidence", help="compile verified campaign records into Markdown and CSV"
    )
    compile_parser.add_argument("--register", required=True, type=Path)
    compile_parser.add_argument("--workspace-root", required=True, type=Path)
    compile_parser.add_argument("--output", required=True, type=Path)
    gcp_plan_parser = subparsers.add_parser(
        "gcp-guardrails-plan", help="render an expiring, resource-free GCP safety bundle"
    )
    gcp_plan_parser.add_argument("--output", required=True, type=Path)
    gcp_plan_parser.add_argument("--generated-at", required=True)
    gcp_verify_parser = subparsers.add_parser(
        "gcp-guardrails-verify", help="verify a current GCP safety bundle"
    )
    gcp_verify_parser.add_argument("--manifest", required=True, type=Path)
    gcp_verify_parser.add_argument("--now", required=True)
    gcp_teardown_parser = subparsers.add_parser(
        "gcp-guardrails-teardown", help="preview exact allowlisted GCP teardown commands"
    )
    gcp_teardown_parser.add_argument("--manifest", required=True, type=Path)
    gcp_teardown_parser.add_argument("--now", required=True)
    args = parser.parse_args(argv)

    def parse_timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return parsed

    if args.command == "gcp-guardrails-plan":
        artifacts = write_guardrail_bundle(
            args.output,
            GuardrailConfig(),
            generated_at=parse_timestamp(args.generated_at),
        )
        print(
            json.dumps(
                {
                    "manifest": str(artifacts.manifest_path),
                    "lifecycle": str(artifacts.lifecycle_path),
                    "resources_created": False,
                }
            )
        )
        return 0

    if args.command == "gcp-guardrails-verify":
        verified = verify_guardrail_bundle(args.manifest, now=parse_timestamp(args.now))
        print(
            json.dumps(
                {
                    "valid": verified["valid"],
                    "valid_until": verified["valid_until"],
                    "resources_created": verified["resources_created"],
                }
            )
        )
        return 0

    if args.command == "gcp-guardrails-teardown":
        verified = verify_guardrail_bundle(
            args.manifest,
            now=parse_timestamp(args.now),
            permit_expired=True,
        )
        print(
            json.dumps(
                {
                    "mode": "dry_run",
                    "resources_changed": False,
                    "project_confirmation_required": verified["teardown"][
                        "execute_requires_project_confirmation"
                    ],
                    "commands": verified["teardown"]["commands"],
                }
            )
        )
        return 0

    if args.command == "register-evidence":
        recorded_command = json.loads(args.command_json)
        if not isinstance(recorded_command, list) or not all(
            isinstance(part, str) for part in recorded_command
        ):
            raise ValueError("command-json must be a JSON array of strings")
        entry = append_run(
            args.register,
            args.workspace_root,
            RunRecord(
                run_id=args.run_id,
                recorded_at=args.recorded_at,
                run_type=args.run_type,
                status=args.status,
                command=recorded_command,
                result_path=args.result,
                artifact_paths=tuple(args.artifact),
                interpretation=args.interpretation,
            ),
        )
        print(json.dumps({"run_id": entry["run_id"], "sequence": entry["sequence"]}))
        return 0

    if args.command == "register-layout-evidence":
        entry = register_layout_result(register_path=args.register, workspace_root=args.workspace_root, run_id=args.run_id, run_type=args.run_type, result_path=args.result, artifact_paths=tuple(args.artifact))
        print(json.dumps({"run_id": entry["run_id"], "sequence": entry["sequence"]}))
        return 0

    if args.command == "compile-evidence":
        entries = read_register(args.register, args.workspace_root)
        outputs = compile_run_tables(args.register, args.workspace_root, args.output)
        print(
            json.dumps(
                {
                    "runs": len(entries),
                    "markdown": str(outputs.markdown_path),
                    "csv": str(outputs.csv_path),
                }
            )
        )
        return 0

    if args.command == "layout-generate":
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError(f"layout output already exists: {args.output}")
        contract = _validated_a0_contract(args.a0_contract)
        source_paths = contract.get("source_paths")
        if not isinstance(source_paths, dict):
            raise ValueError("a0-contract must include source_paths")
        database_value = source_paths.get("database")
        a0_value = source_paths.get("a0")
        if not isinstance(database_value, str) or not isinstance(a0_value, str):
            raise ValueError("a0-contract source_paths must include database and a0 paths")
        source_table = args.source_table or contract.get("source_table")
        if not isinstance(source_table, str):
            raise ValueError("layout-generate requires --source-table or a0-contract source_table")
        specs = {spec.layout_id: spec for spec in layout_specs()}
        manifest = generate_layout(
            specs[args.layout],
            database_path=Path(database_value),
            a0_path=Path(a0_value),
            source_table=source_table,
            output_dir=args.output,
        )
        manifest_path = args.output / "layout-manifest.json"
        manifest["a0_contract"] = {
            "path": str(args.a0_contract.resolve()),
            "sha256": sha256(args.a0_contract.read_bytes()).hexdigest(),
        }
        manifest.update(
            {
                "scope": "gce_local_plain_parquet_layout_generation",
                "storage_format": "plain_parquet",
                "ducklake_involved": False,
                "data_provenance": "assumed_synthetic",
                "execution_environment": {"kind": "gce_local", "storage": "local_pd_balanced"},
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        write_command_record(args.output, "layout-generate")
        print(json.dumps({"manifest": str(manifest_path), "status": manifest["status"]}))
        return 0

    if args.command == "a0-contract":
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError(f"A0 contract output already exists: {args.output}")
        runtime_identity = json.loads(args.runtime_identity.read_text())
        if not isinstance(runtime_identity, dict) or runtime_identity.get("valid") is not True:
            raise ValueError("a0-contract requires a validated runtime-identity manifest")
        raw_controls: dict[str, object] = {}
        if args.historical_controls is not None:
            parsed_controls = json.loads(args.historical_controls.read_text())
            if not isinstance(parsed_controls, dict):
                raise ValueError("historical-controls must be a JSON object")
            raw_controls = parsed_controls
        controls: dict[str, HistoricalControl] = {}
        for name, item in raw_controls.items():
            if not isinstance(item, dict) or "value" not in item or "provenance" not in item:
                raise ValueError(f"historical control {name!r} must contain value and provenance")
            controls[name] = HistoricalControl(value=item["value"], provenance=item["provenance"])
        output_path = args.output / "a0-contract.json"
        args.output.mkdir(parents=True)
        contract = write_a0_contract(
            database_path=args.database,
            a0_path=args.a0,
            historical_controls=controls,
            output_path=output_path,
        )
        contract["source_table"] = args.source_table
        contract["runtime_identity"] = {
            "path": str(args.runtime_identity.resolve()),
            "sha256": sha256(args.runtime_identity.read_bytes()).hexdigest(),
        }
        contract.update(
            {
                "scope": "gce_local_plain_parquet_a0_contract",
                "storage_format": "plain_parquet",
                "ducklake_involved": False,
                "data_provenance": "assumed_synthetic",
                "execution_environment": {"kind": "gce_local", "storage": "local_pd_balanced"},
                "claim_boundary": (
                    "GCE-local Plain-Parquet A0 provenance only; no timing, production data, S3, "
                    "GCS, or DuckLake claim."
                ),
            }
        )
        output_path.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n")
        write_command_record(args.output, "a0-contract")
        print(json.dumps({"contract": str(output_path), "valid": contract["valid"]}))
        return 0

    if args.command == "runtime-identity":
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError(f"runtime identity output already exists: {args.output}")
        result = attest_runtime(
            baseline_deployment_manifest=args.baseline_deployment_manifest,
            runtime_archive=args.runtime_archive,
            duckdb_wheel=args.duckdb_wheel,
            python_path=args.python,
            vendor_root=args.vendor,
        )
        result.update(
            {
                "scope": "gce_local_runtime_identity",
                "ducklake_involved": False,
                "data_provenance": "assumed_synthetic",
                "claim_boundary": "GCE benchmark runtime identity only; no data, timing, production data, GCS, S3, or DuckLake claim.",
            }
        )
        args.output.mkdir(parents=True)
        result_path = args.output / "runtime-identity.json"
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        write_command_record(args.output, "runtime-identity")
        print(json.dumps({"result": str(result_path), "valid": True}))
        return 0

    if args.command == "verify-layout-harness":
        manifest = _json_object(args.manifest, label="layout harness manifest")
        records = manifest.get("members")
        if not isinstance(records, list) or not records:
            raise ValueError("layout harness manifest must declare non-empty members")
        root = args.root.resolve()
        verified = 0
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                raise ValueError("each layout harness member requires a relative path")
            relative = Path(record["path"])
            if relative.is_absolute():
                raise ValueError("layout harness member paths must be relative")
            candidate = (root / relative).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as error:
                raise ValueError("layout harness member escapes root") from error
            identity = _file_identity(candidate)
            if identity["bytes"] != record.get("bytes") or identity["sha256"] != record.get("sha256"):
                raise ValueError(f"layout harness member identity mismatch: {relative}")
            verified += 1
        print(json.dumps({"valid": True, "verified_members": verified, "manifest": str(args.manifest)}))
        return 0

    if args.command == "layout-validate":
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError(f"validation output already exists: {args.output}")
        manifest = json.loads(args.manifest.read_text())
        if not isinstance(manifest, dict):
            raise ValueError("layout manifest must be a JSON object")
        source_paths = manifest.get("source_paths")
        database_value = source_paths.get("database") if isinstance(source_paths, dict) else None
        source_table = manifest.get("source_table")
        if not isinstance(database_value, str) or not isinstance(source_table, str):
            raise ValueError("layout manifest must include source_paths.database and source_table")
        result = validate_layout(
            manifest=manifest,
            representation_root=args.manifest.parent,
            database_path=Path(database_value),
            source_table=source_table,
        )
        result.update(
            {
                "result_version": 1,
                "scope": "gce_local_plain_parquet_layout_validation",
                "engine": "DuckDB",
                "storage_format": "plain_parquet",
                "ducklake_involved": False,
                "data_provenance": "assumed_synthetic",
                "execution_environment": {"kind": "gce_local", "storage": "local_pd_balanced"},
                "layout_manifest": str(args.manifest.resolve()),
                "claim_boundary": (
                    "Untimed local Plain-Parquet correctness and layout validation only; "
                    "no performance, GCP, production data, S3, or DuckLake claim."
                ),
            }
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        write_command_record(args.output.parent, "layout-validate")
        print(json.dumps({"result": str(args.output), "status": result["status"]}))
        return 0

    if args.command == "layout-noise":
        if args.pairs != 19:
            raise ValueError("layout-noise requires exactly 19 paired blocks")
        _require_gce_local_execution()
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError(f"layout noise output already exists: {args.output}")
        contract = _validated_a0_contract(args.a0_contract)
        a0_path = Path(contract["source_paths"]["a0"]).resolve()
        args.output.mkdir(parents=True)
        lanes_dir = args.output / "lanes"
        lanes_dir.mkdir()
        lanes = [lanes_dir / "a0-lane-A.parquet", lanes_dir / "a0-lane-B.parquet"]
        expected = contract["source"]["a0"]
        for lane in lanes:
            shutil.copyfile(a0_path, lane)
            stat = lane.lstat()
            if lane.is_symlink() or stat.st_nlink != 1 or _file_identity(lane) != expected:
                raise ValueError(f"A0 noise lane identity failure: {lane}")
        run = run_paired_screen(
            a0_files=[lanes[0]],
            candidate_files=[lanes[1]],
            variant_id="A0_NOISE",
            blocks=args.pairs,
            warmups=2,
            campaign_seed=args.seed,
        )
        observations = [ScreenObservation(**item) for item in run["observations"]]
        q0_observations = [Q0Observation(**item) for item in run["q0"]]
        noise = summarise_noise(observations, q0_observations=q0_observations)
        result = {
            "result_version": 1,
            "completed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "status": "stable" if run["precision_stable"] and run["q3"]["precision_stable"] else "unstable",
            "scope": "gce_local_plain_parquet_layout_noise",
            "engine": "DuckDB",
            "storage_format": "plain_parquet",
            "ducklake_involved": False,
            "data_provenance": "assumed_synthetic",
            "execution_environment": {"kind": "gce_local", "storage": "local"},
            "a0_contract": {"path": str(args.a0_contract.resolve()), "sha256": sha256(args.a0_contract.read_bytes()).hexdigest()},
            "lanes": [{"path": str(path.resolve()), **_file_identity(path), "copy_method": "shutil.copyfile"} for path in lanes],
            "screen": run,
            "noise": noise,
            "claim_boundary": "GCE-local A0-versus-A0 Plain-Parquet noise control only; no layout-improvement, production data, GCS, S3, or DuckLake claim.",
        }
        result_path = args.output / "layout-noise-result.json"
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        write_command_record(args.output, "layout-noise")
        print(json.dumps({"result": str(result_path), "status": result["status"]}))
        return 0

    if args.command == "layout-screen":
        if args.pairs != 19:
            raise ValueError("layout-screen requires exactly 19 paired blocks")
        _require_gce_local_execution()
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError(f"layout screen output already exists: {args.output}")
        manifest = _json_object(args.layout_manifest, label="layout manifest")
        validation = _json_object(args.validation, label="validation result")
        noise_result = _json_object(args.noise_result, label="noise result")
        if validation.get("valid") is not True:
            raise ValueError("layout-screen requires a validated correctness result")
        noise = noise_result.get("noise")
        if not isinstance(noise, dict):
            raise ValueError("layout-screen requires a noise result with thresholds")
        source_paths = manifest.get("source_paths")
        files = manifest.get("files")
        if not isinstance(source_paths, dict) or not isinstance(source_paths.get("a0"), str) or not isinstance(files, list):
            raise ValueError("layout manifest is missing its A0 path or file inventory")
        candidate_files = [args.layout_manifest.parent / item["path"] for item in files if isinstance(item, dict) and isinstance(item.get("path"), str)]
        if len(candidate_files) != len(files):
            raise ValueError("layout manifest has an invalid file inventory")
        args.output.mkdir(parents=True)
        run = run_paired_screen(
            a0_files=[Path(source_paths["a0"])], candidate_files=candidate_files,
            variant_id=str(manifest.get("layout_id")), blocks=args.pairs, warmups=2, campaign_seed=args.seed,
        )
        eligibility = evaluate_eligibility(correctness_valid=True, summary=run, noise=noise)
        result = {
            "result_version": 1, "completed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "status": eligibility["status"],
            "scope": "gce_local_plain_parquet_layout_screen", "engine": "DuckDB",
            "storage_format": "plain_parquet", "ducklake_involved": False,
            "data_provenance": "assumed_synthetic",
            "execution_environment": {"kind": "gce_local", "storage": "local"},
            "layout_manifest": {"path": str(args.layout_manifest.resolve()), "sha256": sha256(args.layout_manifest.read_bytes()).hexdigest()},
            "validation": {"path": str(args.validation.resolve()), "sha256": sha256(args.validation.read_bytes()).hexdigest()},
            "noise_result": {"path": str(args.noise_result.resolve()), "sha256": sha256(args.noise_result.read_bytes()).hexdigest()},
            "screen": run, "eligibility": eligibility,
            "claim_boundary": "GCE-local Plain-Parquet layout screen for the declared synthetic dataset and query mix only; no production data, GCS, S3, or DuckLake claim.",
        }
        result_path = args.output / "layout-screen-result.json"
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        write_command_record(args.output, "layout-screen")
        print(json.dumps({"result": str(result_path), "status": result["status"]}))
        return 0

    if args.command == "gcs-screen":
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError(f"GCS screen output already exists: {args.output}")
        _require_gce_local_execution()
        manifest = read_gcs_screen_manifest(args.stage_manifest)
        if manifest.bucket != "ducklake-example-project-data":
            raise ValueError("GCS screen bucket is outside the approved assessment scope")
        if manifest.bucket_region != "asia-southeast1":
            raise ValueError("GCS screen bucket must be in asia-southeast1")
        stage_manifest_sha256 = sha256(args.stage_manifest.read_bytes()).hexdigest()
        layout_scope_id = manifest.candidate.layout_id.lower()
        extension_path = args.httpfs_extension.resolve()
        extension_sha256 = sha256(extension_path.read_bytes()).hexdigest()
        a0_relation_sql = render_gcs_read_parquet(manifest.bucket, (manifest.a0_lane_1,))
        candidate_objects = (
            (manifest.a0_lane_2,) if args.mode == "noise" else manifest.candidate.objects
        )
        candidate_relation_sql = render_gcs_read_parquet(manifest.bucket, candidate_objects)

        def setup_gcs(connection: duckdb.DuckDBPyConnection) -> None:
            configure_duckdb_gcs_oauth(
                connection,
                extension_path=extension_path,
                bucket=manifest.bucket,
            )

        if args.mode == "screen":
            if args.noise_result is None:
                raise ValueError("GCS candidate screen requires --noise-result")
            noise_result = _json_object(args.noise_result, label="GCS noise result")
            noise_link = noise_result.get("stage_manifest")
            noise = noise_result.get("noise")
            if (
                noise_result.get("scope") != f"gce_gcs_plain_parquet_{layout_scope_id}_noise"
                or not isinstance(noise_link, dict)
                or noise_link.get("sha256") != stage_manifest_sha256
                or not isinstance(noise, dict)
                or not isinstance(noise.get("aggregate_noise_log"), (int, float))
                or not isinstance(noise.get("q3_noise_log"), (int, float))
            ):
                raise ValueError("GCS candidate screen requires matching remote noise evidence")
        elif args.noise_result is not None:
            raise ValueError("GCS noise control does not accept --noise-result")

        run = run_paired_relation_screen(
            a0_relation_sql=a0_relation_sql,
            candidate_relation_sql=candidate_relation_sql,
            variant_id="A0_GCS_NOISE" if args.mode == "noise" else manifest.candidate.layout_id,
            blocks=19,
            warmups=2,
            bootstrap_samples=2_000,
            campaign_seed=20260809,
            connection_setup=setup_gcs,
        )
        args.output.mkdir(parents=True)
        base = {
            "result_version": 1,
            "completed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "engine": "DuckDB",
            "storage_format": "plain_parquet",
            "ducklake_involved": False,
            "data_provenance": "assumed_synthetic",
            "execution_environment": {
                "kind": "gce_gcs",
                "project_id": "ducklake-example-project",
                "zone": "asia-southeast1-b",
                "vm_name": "ducklake-p2-bench-01",
                "bucket": manifest.bucket,
                "bucket_region": manifest.bucket_region,
                "storage": "gcs_standard_same_region",
                "cache_condition": "warm_default",
            },
            "authentication": {
                "method": "gce_metadata_oauth_bearer",
                "credential_persistence": "none",
                "service_account_access": "bucket_object_viewer_only_during_timing",
            },
            "stage_manifest": {
                "path": str(args.stage_manifest.resolve()),
                "sha256": stage_manifest_sha256,
            },
            "httpfs_extension": {
                "duckdb_version": duckdb.__version__,
                "platform": "linux_amd64",
                "sha256": extension_sha256,
                "load_mode": "explicit_local_path",
                "auto_install_disabled": True,
            },
            "read_relations": {"a0": a0_relation_sql, "candidate": candidate_relation_sql},
            "candidate": {
                "layout_id": manifest.candidate.layout_id,
                "object_count": len(manifest.candidate.objects),
                "physical_bytes": sum(item.physical_bytes for item in manifest.candidate.objects),
            },
        }
        if args.mode == "noise":
            observations = [ScreenObservation(**item) for item in run["observations"]]
            q0_observations = [Q0Observation(**item) for item in run["q0"]]
            noise = summarise_noise(observations, q0_observations=q0_observations)
            result = {
                **base,
                "status": "stable" if run["precision_stable"] and run["q3"]["precision_stable"] else "unstable",
                "scope": f"gce_gcs_plain_parquet_{layout_scope_id}_noise",
                "screen": run,
                "noise": noise,
                "claim_boundary": (
                    "Same-region GCE-to-GCS A0-versus-A0 Plain-Parquet noise control for the "
                    "declared synthetic source only; no layout-improvement, production data, S3, or DuckLake claim."
                ),
            }
            result_path = args.output / "gcs-noise-result.json"
        else:
            eligibility = evaluate_eligibility(correctness_valid=True, summary=run, noise=noise)
            result = {
                **base,
                "status": eligibility["status"],
                "scope": f"gce_gcs_plain_parquet_{layout_scope_id}_screen",
                "noise_result": {
                    "path": str(args.noise_result.resolve()),
                    "sha256": sha256(args.noise_result.read_bytes()).hexdigest(),
                },
                "screen": run,
                "eligibility": eligibility,
                "claim_boundary": (
                    f"Same-region GCE-to-GCS Plain-Parquet A0-versus-{manifest.candidate.layout_id} screen for the declared "
                    "synthetic source and warm/default cache only; no production data, S3, concurrency, or DuckLake claim."
                ),
            }
            result_path = args.output / "gcs-screen-result.json"
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        write_command_record(args.output, "gcs-screen")
        print(json.dumps({"result": str(result_path), "status": result["status"]}))
        return 0

    if args.command == "ducklake-screen":
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError(f"DuckLake screen output already exists: {args.output}")
        _require_gce_local_execution()
        manifest = read_ducklake_c1_manifest(args.stage_manifest)
        if manifest.bucket != "ducklake-example-project-data":
            raise ValueError("DuckLake C1 bucket is outside the approved assessment scope")
        if manifest.bucket_region != "asia-southeast1":
            raise ValueError("DuckLake C1 bucket must be in asia-southeast1")
        stage_manifest_sha256 = sha256(args.stage_manifest.read_bytes()).hexdigest()
        httpfs_extension = args.httpfs_extension.resolve()
        ducklake_extension = args.ducklake_extension.resolve()
        if not httpfs_extension.is_file() or httpfs_extension.is_symlink():
            raise FileNotFoundError(httpfs_extension)
        if not ducklake_extension.is_file() or ducklake_extension.is_symlink():
            raise FileNotFoundError(ducklake_extension)
        direct_clone_increment = args.comparison == "direct_clone_increment"
        baseline_object = manifest.candidate.object if direct_clone_increment else manifest.a0_lane_1
        baseline_relation_sql = render_gcs_read_parquet(manifest.bucket, (baseline_object,))
        candidate_relation_sql = (
            baseline_relation_sql
            if args.mode == "noise" and direct_clone_increment
            else render_gcs_read_parquet(manifest.bucket, (manifest.a0_lane_2,))
            if args.mode == "noise"
            else manifest.catalog.relation_sql
        )
        noise_scope = (
            "gce_gcs_ducklake_c1_direct_noise"
            if direct_clone_increment
            else "gce_gcs_ducklake_c1_noise"
        )
        validation_scope = (
            "gce_gcs_ducklake_c1_direct_correctness"
            if direct_clone_increment
            else "gce_gcs_ducklake_c1_correctness"
        )
        screen_scope = (
            "gce_gcs_ducklake_c1_direct_screen"
            if direct_clone_increment
            else "gce_gcs_ducklake_c1_screen"
        )

        def setup_ducklake_c1(connection: duckdb.DuckDBPyConnection) -> None:
            configure_ducklake_c1_connection(
                connection,
                manifest=manifest,
                httpfs_extension=httpfs_extension,
                ducklake_extension=ducklake_extension,
            )

        if args.mode == "screen":
            if args.noise_result is None:
                raise ValueError("DuckLake C1 screen requires --noise-result")
            if args.validation is None:
                raise ValueError("DuckLake C1 screen requires --validation")
            noise_result = _json_object(args.noise_result, label="DuckLake C1 noise result")
            validation = _json_object(args.validation, label="DuckLake C1 validation result")
            noise_link = noise_result.get("stage_manifest")
            noise = noise_result.get("noise")
            if (
                noise_result.get("scope") != noise_scope
                or not isinstance(noise_link, dict)
                or noise_link.get("sha256") != stage_manifest_sha256
                or not isinstance(noise, dict)
                or not isinstance(noise.get("aggregate_noise_log"), (int, float))
                or not isinstance(noise.get("q3_noise_log"), (int, float))
            ):
                raise ValueError("DuckLake C1 screen requires matching noise evidence")
            validation_link = validation.get("stage_manifest")
            if (
                validation.get("scope") != validation_scope
                or validation.get("status") != "validated"
                or not isinstance(validation_link, dict)
                or validation_link.get("sha256") != stage_manifest_sha256
                or validation.get("valid") is not True
            ):
                raise ValueError("DuckLake C1 screen requires matching validated correctness evidence")
        elif args.noise_result is not None:
            raise ValueError("DuckLake C1 noise control does not accept --noise-result")

        run = run_paired_relation_screen(
            a0_relation_sql=baseline_relation_sql,
            candidate_relation_sql=candidate_relation_sql,
            variant_id=(
                "C1_DIRECT_CLONE_NOISE"
                if args.mode == "noise" and direct_clone_increment
                else "A0_DUCKLAKE_C1_NOISE"
                if args.mode == "noise"
                else "C1_DIRECT_CLONE_INCREMENT"
                if direct_clone_increment
                else "C1"
            ),
            blocks=19,
            warmups=2,
            bootstrap_samples=2_000,
            campaign_seed=20260809,
            connection_setup=setup_ducklake_c1,
        )
        args.output.mkdir(parents=True)
        base = {
            "result_version": 1,
            "completed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "engine": "DuckDB",
            "storage_format": "ducklake_managed_parquet",
            "ducklake_involved": True,
            "data_provenance": "assumed_synthetic",
            "execution_environment": {
                "kind": "gce_gcs",
                "project_id": "ducklake-example-project",
                "zone": "asia-southeast1-b",
                "vm_name": "ducklake-p2-bench-01",
                "bucket": manifest.bucket,
                "bucket_region": manifest.bucket_region,
                "storage": "gcs_standard_same_region",
                "cache_condition": "warm_default",
            },
            "authentication": {
                "method": "gce_metadata_oauth_bearer",
                "credential_persistence": "none",
                "service_account_access": "bucket_object_viewer_only_during_timing",
            },
            "stage_manifest": {
                "path": str(args.stage_manifest.resolve()),
                "sha256": stage_manifest_sha256,
            },
            "httpfs_extension": {
                "duckdb_version": duckdb.__version__,
                "platform": "linux_amd64",
                "sha256": sha256(httpfs_extension.read_bytes()).hexdigest(),
                "load_mode": "explicit_local_path",
                "auto_install_disabled": True,
            },
            "ducklake_extension": {
                "duckdb_version": duckdb.__version__,
                "platform": "linux_amd64",
                "sha256": sha256(ducklake_extension.read_bytes()).hexdigest(),
                "load_mode": "explicit_local_path",
                "auto_install_disabled": True,
            },
            "catalog": {
                "type": "duckdb_local_single_client",
                "metadata_path": str(manifest.catalog.metadata_path),
                "metadata_sha256": manifest.catalog.metadata_sha256,
                "relation": manifest.catalog.relation_sql,
                "read_only_during_timing": True,
            },
            "source_b1": {
                "object": manifest.source_b1.object,
                "generation": manifest.source_b1.generation,
                "sha256": manifest.source_b1.sha256,
                "physical_bytes": manifest.source_b1.physical_bytes,
                "crc32c": manifest.source_b1.crc32c,
            },
            "candidate": {
                "layout_id": manifest.candidate.layout_id,
                "management_mode": manifest.candidate.management_mode,
                "object": manifest.candidate.object.object,
                "generation": manifest.candidate.object.generation,
                "sha256": manifest.candidate.object.sha256,
                "physical_bytes": manifest.candidate.object.physical_bytes,
                "crc32c": manifest.candidate.object.crc32c,
                "physical_contract": "byte_identical_to_b1",
            },
            "comparison": {
                "kind": args.comparison,
                "baseline": (
                    "direct_generation_pinned_c1_clone"
                    if direct_clone_increment
                    else "a0_lane_1"
                ),
                "candidate": (
                    "direct_generation_pinned_c1_clone"
                    if args.mode == "noise" and direct_clone_increment
                    else "a0_lane_2"
                    if args.mode == "noise"
                    else "ducklake_catalogue_relation"
                ),
            },
            "read_relations": {"baseline": baseline_relation_sql, "candidate": candidate_relation_sql},
        }
        if args.mode == "noise":
            observations = [ScreenObservation(**item) for item in run["observations"]]
            q0_observations = [Q0Observation(**item) for item in run["q0"]]
            result = {
                **base,
                "status": "stable" if run["precision_stable"] and run["q3"]["precision_stable"] else "unstable",
                "scope": noise_scope,
                "screen": run,
                "noise": summarise_noise(observations, q0_observations=q0_observations),
                "claim_boundary": (
                    "Same-region GCE-to-GCS direct-generation-pinned-C1-clone-versus-itself control "
                    "with the explicit DuckLake C1 runtime setup; synthetic evidence only, with no "
                    "production data, S3 or concurrency claim."
                    if direct_clone_increment
                    else "Same-region GCE-to-GCS A0-versus-A0 control with the explicit DuckLake C1 "
                    "runtime setup; synthetic evidence only, with no production data, S3 or concurrency claim."
                ),
            }
            result_path = args.output / "ducklake-noise-result.json"
        else:
            eligibility = evaluate_eligibility(correctness_valid=True, summary=run, noise=noise)
            result = {
                **base,
                "status": eligibility["status"],
                "scope": screen_scope,
                "noise_result": {
                    "path": str(args.noise_result.resolve()),
                    "sha256": sha256(args.noise_result.read_bytes()).hexdigest(),
                },
                "validation": {
                    "path": str(args.validation.resolve()),
                    "sha256": sha256(args.validation.read_bytes()).hexdigest(),
                },
                "screen": run,
                "eligibility": eligibility,
                "claim_boundary": (
                    "Same-region GCE-to-GCS DuckLake C1 catalogue versus a direct generation-pinned read "
                    "of the exact same registered Parquet object. This is a single-client synthetic "
                    "management-layer comparison under warm/default cache only; no production data, S3, concurrency "
                    "or physical-layout claim."
                    if direct_clone_increment
                    else "Broader same-region GCE-to-GCS A0-versus-DuckLake-C1 representation comparison "
                    "for the declared synthetic source and warm/default cache only. It combines a physical "
                    "representation change with catalogue management and is not an incremental DuckLake claim; "
                    "no production data, S3 or concurrency claim."
                ),
            }
            result_path = args.output / "ducklake-screen-result.json"
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        write_command_record(args.output, "ducklake-screen")
        print(json.dumps({"result": str(result_path), "status": result["status"]}))
        return 0

    if args.command == "ducklake-validate-c1":
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError(f"DuckLake C1 validation output already exists: {args.output}")
        _require_gce_local_execution()
        stage_manifest_sha256 = sha256(args.stage_manifest.read_bytes()).hexdigest()
        try:
            validation = validate_ducklake_c1(
                manifest_path=args.stage_manifest,
                httpfs_extension=args.httpfs_extension,
                ducklake_extension=args.ducklake_extension,
                comparison=args.comparison,
            )
            status = "validated" if validation["valid"] else "invalid"
        except Exception:
            args.output.mkdir(parents=True, exist_ok=True)
            validation = {"valid": False, "reason": "DuckLake C1 validation failed; catalog and credential details redacted"}
            status = "unsupported"
        args.output.mkdir(parents=True, exist_ok=True)
        result = {
            "result_version": 1,
            "completed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "status": status,
            "valid": validation["valid"],
            "scope": (
                "gce_gcs_ducklake_c1_direct_correctness"
                if args.comparison == "direct_clone_increment"
                else "gce_gcs_ducklake_c1_correctness"
            ),
            "comparison": args.comparison,
            "ducklake_involved": True,
            "data_provenance": "assumed_synthetic",
            "stage_manifest": {
                "path": str(args.stage_manifest.resolve()),
                "sha256": stage_manifest_sha256,
            },
            "httpfs_extension": _file_identity(args.httpfs_extension.resolve()),
            "ducklake_extension": _file_identity(args.ducklake_extension.resolve()),
            "validation": validation,
            "claim_boundary": (
                "Untimed DuckLake C1 direct-generation-pinned-clone versus catalogue correctness and "
                "physical-order evidence for a synthetic byte-identical clone; no performance, production data, "
                "S3 or concurrency claim."
                if args.comparison == "direct_clone_increment"
                else "Untimed DuckLake C1 correctness and physical-order evidence for a synthetic "
                "byte-identical clone; no performance, production data, S3 or concurrency claim."
            ),
        }
        result_path = args.output / "ducklake-validation-result.json"
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        write_command_record(args.output, "ducklake-validate-c1")
        print(json.dumps({"result": str(result_path), "status": status}))
        return 0

    if args.command == "ducklake-bootstrap-c1":
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError(f"DuckLake C1 bootstrap output already exists: {args.output}")
        _require_gce_local_execution()
        try:
            bootstrap = bootstrap_ducklake_c1(
                plan_path=args.plan,
                output=args.output,
                httpfs_extension=args.httpfs_extension,
                ducklake_extension=args.ducklake_extension,
            )
            status = "validated"
        except Exception:
            args.output.mkdir(parents=True, exist_ok=True)
            bootstrap = {
                "status": "unsupported",
                "reason": "DuckLake C1 bootstrap failed; catalog and credential details redacted",
            }
            status = "unsupported"
        args.output.mkdir(parents=True, exist_ok=True)
        result = {
            "result_version": 1,
            "completed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "status": status,
            "scope": "gce_gcs_ducklake_c1_bootstrap",
            "ducklake_involved": True,
            "data_provenance": "assumed_synthetic",
            "plan": {
                "path": str(args.plan.resolve()),
                "sha256": sha256(args.plan.read_bytes()).hexdigest(),
            },
            "httpfs_extension": _file_identity(args.httpfs_extension.resolve()),
            "ducklake_extension": _file_identity(args.ducklake_extension.resolve()),
            "bootstrap": bootstrap,
            "claim_boundary": (
                "DuckLake C1 metadata bootstrap over a declared synthetic byte-identical Parquet clone; "
                "no timing, production data, S3, concurrency or physical-layout performance claim."
            ),
        }
        result_path = args.output / "ducklake-bootstrap-result.json"
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        write_command_record(args.output, "ducklake-bootstrap-c1")
        print(json.dumps({"result": str(result_path), "status": status}))
        return 0

    if args.command == "calibrate-gcs":
        if args.output.exists():
            raise FileExistsError(f"calibration output already exists: {args.output}")
        manifest = read_gcs_manifest(args.source_manifest)
        if manifest.bucket != "ducklake-example-project-data":
            raise ValueError("GCS calibration bucket is outside the approved assessment scope")
        if manifest.bucket_region != "asia-southeast1":
            raise ValueError("GCS calibration bucket must be in asia-southeast1")
        extension_path = args.httpfs_extension.resolve()
        extension_sha256 = sha256(extension_path.read_bytes()).hexdigest()
        lanes = RemoteCalibrationLanes(
            lane_1_uri=gcs_media_url(manifest.bucket, manifest.lane_1.object),
            lane_2_uri=gcs_media_url(manifest.bucket, manifest.lane_2.object),
            lane_1_sha256=manifest.lane_1.sha256,
            lane_2_sha256=manifest.lane_2.sha256,
            physical_bytes_per_lane=manifest.physical_bytes_per_lane,
        )

        def setup_gcs(connection: duckdb.DuckDBPyConnection) -> None:
            configure_duckdb_gcs_oauth(
                connection,
                extension_path=extension_path,
                bucket=manifest.bucket,
            )

        config = CalibrationConfig(
            warmups=args.warmups,
            initial_pairs=args.initial_pairs,
            pair_increment=args.pair_increment,
            max_pairs=args.max_pairs,
            bootstrap_samples=args.bootstrap_samples,
            precision_ratio=args.precision_ratio,
        )
        calibration = calibrate_parquet_uris(
            lanes,
            config=config,
            seed=args.seed,
            specs=query_specs(),
            connection_setup=setup_gcs,
        )
        args.output.mkdir(parents=True)
        results_path = args.output / "calibration-results.json"
        results = {
            "result_version": 3,
            "scope": "gce_gcs_a0_vs_a0_noise_calibration",
            "engine": "DuckDB",
            "storage_format": "plain_parquet",
            "ducklake_involved": False,
            "execution_environment": {
                "kind": "gce_gcs",
                "project_id": "ducklake-example-project",
                "zone": "asia-southeast1-b",
                "vm_name": "ducklake-p2-bench-01",
                "bucket": manifest.bucket,
                "bucket_region": manifest.bucket_region,
                "storage": "gcs_standard_same_region",
            },
            "authentication": {
                "method": "gce_metadata_oauth_bearer",
                "credential_persistence": "none",
                "service_account_access": "bucket_object_viewer_only",
            },
            "source_objects": json.loads(args.source_manifest.read_text()),
            "httpfs_extension": {
                "duckdb_version": duckdb.__version__,
                "platform": "linux_amd64",
                "sha256": extension_sha256,
                "load_mode": "explicit_local_path",
                "auto_install_disabled": True,
            },
            "query_definitions": [asdict(spec) for spec in query_specs()],
            "calibration": calibration,
            "environment": {
                "python_version": sys.version.split()[0],
                "platform": platform.platform(),
                "duckdb_version": duckdb.__version__,
            },
            "claim_boundary": (
                "Same-region GCE-to-GCS A0-versus-A0 timing-noise evidence only; "
                "no layout-improvement, production data, S3, or DuckLake claim."
            ),
        }
        results_path.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
        all_stable = all(
            query["status"] == "stable" for query in calibration["queries"].values()
        )
        print(json.dumps({"results": str(results_path), "all_stable": all_stable}))
        return 0

    profile = SyntheticProfile(
        rows=args.rows,
        seed=args.seed,
        scenario=Scenario(args.scenario),
    )

    if args.command == "calibrate-local":
        if args.output.exists():
            raise FileExistsError(f"calibration output already exists: {args.output}")
        config = CalibrationConfig(
            warmups=args.warmups,
            initial_pairs=args.initial_pairs,
            pair_increment=args.pair_increment,
            max_pairs=args.max_pairs,
            bootstrap_samples=args.bootstrap_samples,
            precision_ratio=args.precision_ratio,
        )
        artifacts = build_local_fixture(args.output / "fixture", profile)
        lanes = prepare_identical_lanes(artifacts.parquet_path, args.output / "lanes")
        calibration = calibrate_parquet_pair(
            lanes,
            config=config,
            seed=args.seed,
            specs=query_specs(),
        )
        results_path = args.output / "calibration-results.json"
        provenance = CALIBRATION_ENVIRONMENTS[args.environment_scope]
        results = {
            "result_version": 2,
            "scope": provenance["scope"],
            "engine": "DuckDB",
            "storage_format": "plain_parquet",
            "ducklake_involved": False,
            "execution_environment": provenance["execution_environment"],
            "profile": profile.to_manifest(),
            "fixture_manifest": json.loads(artifacts.manifest_path.read_text()),
            "query_definitions": [asdict(spec) for spec in query_specs()],
            "calibration": calibration,
            "environment": {
                "python_version": sys.version.split()[0],
                "platform": platform.platform(),
                "duckdb_version": duckdb.__version__,
            },
            "claim_boundary": provenance["claim_boundary"],
        }
        results_path.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
        all_stable = all(
            query["status"] == "stable" for query in calibration["queries"].values()
        )
        print(json.dumps({"results": str(results_path), "all_stable": all_stable}))
        return 0

    artifacts = build_local_fixture(args.output, profile)
    parquet_path = str(artifacts.parquet_path).replace("'", "''")
    with duckdb.connect(str(artifacts.database_path)) as connection:
        connection.execute(
            f"CREATE TEMP VIEW parquet_data AS SELECT * FROM read_parquet('{parquet_path}')"
        )
        correctness = validate_relations(connection, "synthetic_data", "parquet_data")
        native_queries = run_query_suite(connection, "synthetic_data")
        parquet_queries = run_query_suite(connection, "parquet_data")
        extension_columns = [
            "extension_name",
            "loaded",
            "installed",
            "extension_version",
            "install_mode",
            "installed_from",
        ]
        extensions = [
            dict(zip(extension_columns, row, strict=True))
            for row in connection.execute(
                """
                SELECT extension_name, loaded, installed, extension_version, install_mode,
                       installed_from
                FROM duckdb_extensions()
                WHERE loaded OR installed
                ORDER BY extension_name
                """
            ).fetchall()
        ]

    fixture_manifest = json.loads(artifacts.manifest_path.read_text())
    results_path = args.output / "smoke-results.json"
    results = {
        "result_version": 1,
        "scope": "local_synthetic_functional_only",
        "working_assumption": "native_duckdb_reference_plus_separate_parquet",
        "profile": profile.to_manifest(),
        "fixture_manifest": fixture_manifest,
        "correctness": correctness,
        "query_definitions": [asdict(spec) for spec in query_specs()],
        "queries": {"A0": parquet_queries, "D": native_queries},
        "environment": {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "duckdb_version": duckdb.__version__,
            "extensions": extensions,
        },
        "provenance": {"part1_poc_sha256": hash_tree(args.part1_reference)},
        "claim_boundary": (
            "Functional local synthetic evidence only; no timing, GCP, production data, S3, or DuckLake claim."
        ),
    }
    results_path.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"results": str(results_path), "valid": correctness["valid"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
