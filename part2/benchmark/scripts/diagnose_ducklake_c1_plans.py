"""Record untimed DuckDB plans for the causal C1 direct-clone comparison.

This is an explanatory diagnostic, not an additional performance measurement.  It makes no
data, metadata, DuckLake catalogue or cloud-object mutation.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import duckdb

from ducklake_benchmark.ducklake_c1 import (
    configure_ducklake_c1_connection,
    read_ducklake_c1_manifest,
)
from ducklake_benchmark.gcs import render_gcs_read_parquet
from ducklake_benchmark.queries import query_specs


def _explain(connection: duckdb.DuckDBPyConnection, sql: str) -> list[list[object]]:
    """Return the complete textual EXPLAIN rows without running EXPLAIN ANALYZE."""

    return [list(row) for row in connection.execute(f"EXPLAIN {sql}").fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--httpfs-extension", required=True, type=Path)
    parser.add_argument("--ducklake-extension", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(f"plan-diagnostic output already exists: {args.output}")

    manifest = read_ducklake_c1_manifest(args.stage_manifest)
    direct_relation = render_gcs_read_parquet(manifest.bucket, (manifest.candidate.object,))
    connection = duckdb.connect()
    try:
        configure_ducklake_c1_connection(
            connection,
            manifest=manifest,
            httpfs_extension=args.httpfs_extension,
            ducklake_extension=args.ducklake_extension,
        )
        connection.execute(f"CREATE TEMP VIEW direct_c1 AS SELECT * FROM {direct_relation}")
        plans: dict[str, dict[str, object]] = {}
        for spec in query_specs():
            direct_sql = spec.sql_template.format(relation="direct_c1")
            ducklake_sql = spec.sql_template.format(relation=manifest.catalog.relation_sql)
            plans[spec.query_id] = {
                "primary": spec.primary,
                "direct_parquet": _explain(connection, direct_sql),
                "ducklake_catalogue": _explain(connection, ducklake_sql),
            }
    finally:
        connection.close()

    args.output.mkdir(parents=True)
    result = {
        "result_version": 1,
        "completed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "scope": "gce_gcs_ducklake_c1_direct_explain_diagnostic",
        "timed": False,
        "comparison": "direct_clone_increment",
        "stage_manifest": {
            "path": str(args.stage_manifest.resolve()),
            "sha256": sha256(args.stage_manifest.read_bytes()).hexdigest(),
        },
        "direct_object": manifest.candidate.object.__dict__,
        "catalog_relation": manifest.catalog.relation_sql,
        "plans": plans,
        "claim_boundary": (
            "Untimed C1 logical-plan diagnostic only. It may support a mechanism hypothesis but does "
            "not establish remote bytes, requests, cache behavior or a performance result."
        ),
    }
    path = args.output / "ducklake-c1-explain-diagnostic.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"result": str(path), "timed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
