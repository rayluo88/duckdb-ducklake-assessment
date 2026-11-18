"""Run the bounded Cm multi-file DuckLake sensitivity on GCE.

This is deliberately an operational, one-stage runner rather than a change to
the frozen C1 contract.  It compares an explicit, generation-pinned list of
the eight B2 objects with a local read-only DuckLake catalogue registering
those exact URLs.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import duckdb

from ducklake_benchmark.gcs import (
    GcsObject,
    configure_duckdb_gcs_oauth,
    gcs_media_url_for_generation,
    render_gcs_read_parquet,
)
from ducklake_benchmark.layout_screen import (
    Q0Observation,
    ScreenObservation,
    evaluate_eligibility,
    run_paired_relation_screen,
    summarise_noise,
)
from ducklake_benchmark.correctness import CorrectnessMismatch, validate_relations
from ducklake_benchmark.queries import run_query_suite


BUCKET = "ducklake-example-project-data"
PREFIX = "gcs-ducklake-cm-20260809/cm/"
OBJECTS = (
    ("part_0.parquet", "1786288812744912", 128639439, "yOIlQw==", "8ac3353afbba88a4d7d96115dd9da500e2650a77399ffaa7ed1658f051372e9b"),
    ("part_1.parquet", "1786288812741671", 128640397, "utmcNA==", "53e9dbc4a6092ce82474be0db0d8d0fe62371b082e05bc8306d19a02e32f3a00"),
    ("part_2.parquet", "1786288812543550", 128641227, "EtIJSQ==", "b8095cabdc9b3535f535de3f6b519fa27ab511cebd17a2089aeba2568162bc6c"),
    ("part_3.parquet", "1786288812744597", 128639981, "Y8l4fg==", "7a8d1a186a751726427ea328223fba91f86674c5d7c42999b56d6c9d38ffafc5"),
    ("part_4.parquet", "1786288812543089", 128639773, "VBib+w==", "7c56c5bc5169434b936cdf5e2d567171f6629ae19739fb73141571fabf050fa6"),
    ("part_5.parquet", "1786288812741131", 128639842, "pf4FBA==", "8569eb85968bffc0278a752d0bfb5150fce060d5ce1858efc6a0f979e3dcafd7"),
    ("part_6.parquet", "1786288812659256", 128638485, "eXvFew==", "1e527158dfe61b184ff265cf2b763280456b79a27a1eb56622cbff2af081dc82"),
    ("part_7.parquet", "1786288811357786", 85704443, "FTy2ww==", "f7c92f5a9e7c5be8d8db875bdad065540821f2cd427efeac370fd858cc340d85"),
)


def objects() -> tuple[GcsObject, ...]:
    return tuple(
        GcsObject(
            object=PREFIX + name, generation=generation, physical_bytes=size,
            crc32c=crc32c, sha256=digest,
        )
        for name, generation, size, crc32c, digest in OBJECTS
    )


def configure(connection: duckdb.DuckDBPyConnection, httpfs: Path, ducklake: Path) -> None:
    configure_duckdb_gcs_oauth(connection, extension_path=httpfs, bucket=BUCKET)
    connection.execute("SET autoinstall_known_extensions = false")
    connection.execute(f"LOAD '{str(ducklake.resolve()).replace(chr(39), chr(39) * 2)}'")
    connection.execute("SET autoload_known_extensions = false")


def bootstrap(output: Path, httpfs: Path, ducklake: Path) -> tuple[Path, str]:
    metadata = output / "metadata.ducklake"
    connection = duckdb.connect()
    try:
        configure(connection, httpfs, ducklake)
        unused = output / "unused-data-path"
        connection.execute(f"ATTACH 'ducklake:{metadata}' AS cm (DATA_PATH '{unused}')")
        connection.execute("CALL cm.set_option('data_inlining_row_limit', 0)")
        connection.execute("USE cm")
        direct = render_gcs_read_parquet(BUCKET, objects())
        connection.execute(f"CREATE TABLE b2 AS SELECT * FROM {direct} LIMIT 0")
        urls = [gcs_media_url_for_generation(BUCKET, item.object, item.generation) for item in objects()]
        for url in urls:
            escaped = url.replace("'", "''")
            connection.execute("CALL ducklake_add_data_files('cm', 'b2', '" + escaped + "')")
        count = connection.execute("SELECT count(*) FROM ducklake_list_files('cm', 'b2', schema => 'main')").fetchone()[0]
        if count != 8:
            raise ValueError(f"Cm must register exactly eight files, got {count}")
    finally:
        connection.close()
    return metadata, sha256(metadata.read_bytes()).hexdigest()


def validate(metadata: Path, httpfs: Path, ducklake: Path) -> dict[str, object]:
    connection = duckdb.connect()
    try:
        configure(connection, httpfs, ducklake)
        connection.execute(f"ATTACH 'ducklake:{metadata}' AS cm (READ_ONLY)")
        direct = render_gcs_read_parquet(BUCKET, objects())
        connection.execute(f"CREATE TEMP VIEW direct_parquet AS SELECT * FROM {direct}")
        connection.execute("CREATE TEMP VIEW ducklake_cm AS SELECT * FROM cm.main.b2")
        try:
            equivalent = validate_relations(connection, "direct_parquet", "ducklake_cm")
        except CorrectnessMismatch as error:
            equivalent = {"valid": False, "error": str(error)}
        queries_match = run_query_suite(connection, "direct_parquet") == run_query_suite(connection, "ducklake_cm")
    finally:
        connection.close()
    return {"valid": equivalent.get("valid") is True and queries_match, "value_equivalence": equivalent, "query_results_match": queries_match}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--httpfs-extension", required=True, type=Path)
    parser.add_argument("--ducklake-extension", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    metadata, metadata_sha = bootstrap(args.output, args.httpfs_extension, args.ducklake_extension)
    validation = validate(metadata, args.httpfs_extension, args.ducklake_extension)
    manifest = {"stage_id": "P2-GCE-GCS-DUCKLAKE-CM-MULTIFILE-20260809-001", "bucket": BUCKET, "objects": [item.__dict__ for item in objects()], "metadata_path": str(metadata.resolve()), "metadata_sha256": metadata_sha, "catalog_relation": "cm.main.b2", "validation": validation}
    (args.output / "cm-stage-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    if not validation["valid"]:
        print(json.dumps({"status": "invalid", "output": str(args.output)}))
        return
    direct = render_gcs_read_parquet(BUCKET, objects())
    def setup(connection: duckdb.DuckDBPyConnection) -> None:
        configure(connection, args.httpfs_extension, args.ducklake_extension)
        connection.execute(f"ATTACH 'ducklake:{metadata}' AS cm (READ_ONLY)")
    noise_run = run_paired_relation_screen(a0_relation_sql=direct, candidate_relation_sql=direct, variant_id="CM_DIRECT_NOISE", connection_setup=setup)
    noise = summarise_noise([ScreenObservation(**item) for item in noise_run["observations"]], q0_observations=[Q0Observation(**item) for item in noise_run["q0"]])
    screen = run_paired_relation_screen(a0_relation_sql=direct, candidate_relation_sql="cm.main.b2", variant_id="CM_DIRECT_INCREMENT", connection_setup=setup)
    eligibility = evaluate_eligibility(correctness_valid=True, summary=screen, noise=noise)
    result = {"completed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "status": eligibility["status"], "scope": "gce_gcs_ducklake_cm_direct_multifile_sensitivity", "comparison": "direct_generation_pinned_eight_file_b2_vs_ducklake_same_files", "validation": validation, "noise": noise, "screen": screen, "eligibility": eligibility, "manifest": manifest, "claim_boundary": "Synthetic same-region GCE/GCS eight-file B2 sensitivity only; no production data, S3, concurrency, production, or general DuckLake claim."}
    (args.output / "cm-screen-result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": result["status"], "result": str(args.output / "cm-screen-result.json")}))


if __name__ == "__main__":
    main()
