from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb


def _sql_string(value: Path | str) -> str:
    return str(value).replace("'", "''")


def _reset_known_outputs(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("plain", "ducklake"):
        target = output_dir / name
        if target.exists():
            shutil.rmtree(target)
    for name in ("synthetic_measurements.csv", "results.json"):
        target = output_dir / name
        if target.exists():
            target.unlink()


def build_synthetic_csv(csv_path: Path, row_count: int = 600) -> int:
    """Create deterministic, non-identifying synthetic measurements."""
    if row_count < 1:
        raise ValueError("row_count must be positive")

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    cohorts = ("HELIOS", "SPHS", "SEED")
    fieldnames = (
        "participant_id",
        "cohort",
        "visit_date",
        "age_years",
        "biomarker_value",
        "review_flag",
    )

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index in range(1, row_count + 1):
            cohort_index = (index - 1) % len(cohorts)
            writer.writerow(
                {
                    "participant_id": f"S{index:05d}",
                    "cohort": cohorts[cohort_index],
                    "visit_date": (
                        date(2026, 1, 1) + timedelta(days=index % 180)
                    ).isoformat(),
                    "age_years": 20 + (index % 61),
                    "biomarker_value": f"{50 + (index % 37) * 0.25 + cohort_index * 1.5:.2f}",
                    "review_flag": "true" if index % 17 == 0 else "false",
                }
            )
    return row_count


def _summary(connection: duckdb.DuckDBPyConnection, relation: str) -> dict[str, Any]:
    row = connection.execute(
        f"""
        SELECT
            count(*)::BIGINT AS row_count,
            round(avg(biomarker_value), 4) AS average_biomarker,
            sum(CASE WHEN review_flag THEN 1 ELSE 0 END)::BIGINT AS review_flags,
            count(DISTINCT cohort)::BIGINT AS cohort_count
        FROM {relation}
        """
    ).fetchone()
    assert row is not None
    return {
        "row_count": row[0],
        "average_biomarker": row[1],
        "review_flags": row[2],
        "cohort_count": row[3],
    }


def _schema(
    connection: duckdb.DuckDBPyConnection, relation: str
) -> list[dict[str, str]]:
    rows = connection.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
    return [{"column": row[0], "type": row[1]} for row in rows]


def _load_ducklake(connection: duckdb.DuckDBPyConnection) -> str:
    connection.execute("INSTALL ducklake")
    connection.execute("LOAD ducklake")
    row = connection.execute(
        """
        SELECT extension_version
        FROM duckdb_extensions()
        WHERE extension_name = 'ducklake'
        """
    ).fetchone()
    assert row is not None and row[0]
    return row[0]


def run_comparison(output_dir: Path, row_count: int = 600) -> dict[str, Any]:
    """Run the raw CSV, plain Parquet, and DuckLake-managed Parquet comparison."""
    output_dir = output_dir.resolve()
    _reset_known_outputs(output_dir)

    csv_path = output_dir / "synthetic_measurements.csv"
    plain_dir = output_dir / "plain"
    plain_parquet = plain_dir / "measurements.parquet"
    ducklake_dir = output_dir / "ducklake"
    catalog_path = ducklake_dir / "catalog.ducklake"
    ducklake_data = ducklake_dir / "data"

    plain_dir.mkdir(parents=True, exist_ok=True)
    ducklake_data.mkdir(parents=True, exist_ok=True)
    build_synthetic_csv(csv_path, row_count=row_count)

    csv_sql = _sql_string(csv_path)
    parquet_sql = _sql_string(plain_parquet)
    catalog_sql = _sql_string(catalog_path)
    ducklake_data_sql = _sql_string(ducklake_data)

    connection = duckdb.connect()
    ducklake_extension_version = _load_ducklake(connection)
    duckdb_version = duckdb.__version__

    raw_relation = f"read_csv_auto('{csv_sql}', header = true)"
    raw_summary = _summary(connection, raw_relation)
    raw_schema = _schema(connection, raw_relation)

    connection.execute(
        f"""
        COPY (SELECT * FROM {raw_relation})
        TO '{parquet_sql}' (FORMAT PARQUET)
        """
    )
    parquet_relation = f"read_parquet('{parquet_sql}')"
    parquet_summary = _summary(connection, parquet_relation)
    parquet_schema = _schema(connection, parquet_relation)

    connection.execute(
        f"""
        ATTACH 'ducklake:{catalog_sql}' AS lake
        (DATA_PATH '{ducklake_data_sql}')
        """
    )
    connection.execute(
        f"""
        CREATE TABLE lake.measurements AS
        SELECT * FROM {raw_relation}
        """
    )
    ducklake_initial_summary = _summary(connection, "lake.measurements")
    ducklake_schema = _schema(connection, "lake.measurements")

    snapshot_count_before_update = connection.execute(
        "SELECT count(*) FROM lake.snapshots()"
    ).fetchone()[0]
    initial_snapshot_id = connection.execute(
        "SELECT max(snapshot_id) FROM lake.snapshots()"
    ).fetchone()[0]
    initial_value = connection.execute(
        """
        SELECT biomarker_value
        FROM lake.measurements
        WHERE participant_id = 'S00001'
        """
    ).fetchone()[0]

    connection.execute(
        """
        UPDATE lake.measurements
        SET biomarker_value = 99.99
        WHERE participant_id = 'S00001'
        """
    )
    snapshot_count_after_update = connection.execute(
        "SELECT count(*) FROM lake.snapshots()"
    ).fetchone()[0]
    updated_value = connection.execute(
        """
        SELECT biomarker_value
        FROM lake.measurements
        WHERE participant_id = 'S00001'
        """
    ).fetchone()[0]
    historical_value = connection.execute(
        f"""
        SELECT biomarker_value
        FROM lake.measurements AT (VERSION => {initial_snapshot_id})
        WHERE participant_id = 'S00001'
        """
    ).fetchone()[0]

    connection.close()

    reopened = duckdb.connect()
    _load_ducklake(reopened)
    reopened.execute(
        f"""
        ATTACH 'ducklake:{catalog_sql}' AS lake
        (DATA_PATH '{ducklake_data_sql}')
        """
    )
    reopened_row_count = reopened.execute(
        "SELECT count(*) FROM lake.measurements"
    ).fetchone()[0]
    reopened_value = reopened.execute(
        """
        SELECT biomarker_value
        FROM lake.measurements
        WHERE participant_id = 'S00001'
        """
    ).fetchone()[0]
    reopened.close()

    ducklake_parquet_files = sorted(ducklake_data.rglob("*.parquet"))
    results: dict[str, Any] = {
        "duckdb_version": duckdb_version,
        "ducklake_extension_version": ducklake_extension_version,
        "row_count": row_count,
        "baseline_results_equal": (
            raw_summary == parquet_summary == ducklake_initial_summary
        ),
        "raw_csv": {
            "summary": raw_summary,
            "schema": raw_schema,
            "file_size_bytes": csv_path.stat().st_size,
        },
        "plain_parquet": {
            "summary": parquet_summary,
            "schema": parquet_schema,
            "file_size_bytes": plain_parquet.stat().st_size,
        },
        "ducklake": {
            "initial_summary": ducklake_initial_summary,
            "schema": ducklake_schema,
            "snapshot_count_before_update": snapshot_count_before_update,
            "snapshot_count_after_update": snapshot_count_after_update,
            "initial_snapshot_id": initial_snapshot_id,
            "initial_value": initial_value,
            "updated_value": updated_value,
            "historical_value": historical_value,
            "reopened_row_count": reopened_row_count,
            "reopened_value": reopened_value,
            "catalog_file_exists": catalog_path.exists(),
            "catalog_file_size_bytes": catalog_path.stat().st_size,
            "parquet_file_count": len(ducklake_parquet_files),
            "parquet_total_size_bytes": sum(
                path.stat().st_size for path in ducklake_parquet_files
            ),
            "parquet_files": [
                str(path.relative_to(output_dir)) for path in ducklake_parquet_files
            ],
        },
        "limits": [
            "Synthetic local single-client test; no S3 credentials or network path exercised.",
            "No performance benchmark or production-scale data volume.",
            "No multi-user concurrency, IAM, audit, backup, recovery, or failure testing.",
            "File sizes are descriptive for this dataset and not general performance evidence.",
        ],
    }
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare raw CSV, plain Parquet, and DuckLake-managed Parquet."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("generated"),
        help="Directory for synthetic data and experiment artifacts.",
    )
    parser.add_argument("--rows", type=int, default=600)
    args = parser.parse_args()

    results = run_comparison(args.output_dir, row_count=args.rows)
    results_path = args.output_dir.resolve() / "results.json"
    results_path.write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
