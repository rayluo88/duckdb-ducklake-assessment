from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

import duckdb


def sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


with tempfile.TemporaryDirectory(prefix="ducklake-m4-") as temporary_directory:
    root = Path(temporary_directory)
    csv_path = root / "measurements.csv"
    parquet_path = root / "measurements.parquet"
    catalog_path = root / "catalog.ducklake"
    data_path = root / "data"
    data_path.mkdir()

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sample_id", "cohort", "value"])
        writer.writeheader()
        writer.writerows(
            {
                "sample_id": f"S{index:05d}",
                "cohort": "A" if index % 2 else "B",
                "value": str(float(index)),
            }
            for index in range(1, 601)
        )

    csv_sql = sql_path(csv_path)
    parquet_sql = sql_path(parquet_path)
    catalog_sql = sql_path(catalog_path)
    data_sql = sql_path(data_path)

    connection = duckdb.connect()
    connection.execute("INSTALL ducklake")
    connection.execute("LOAD ducklake")

    raw_result = connection.execute(
        f"""
        SELECT count(*)::BIGINT, sum(value)::DOUBLE
        FROM read_csv_auto('{csv_sql}', header = true)
        """
    ).fetchone()

    connection.execute(
        f"""
        COPY (SELECT * FROM read_csv_auto('{csv_sql}', header = true))
        TO '{parquet_sql}' (FORMAT PARQUET)
        """
    )
    parquet_result = connection.execute(
        f"SELECT count(*)::BIGINT, sum(value)::DOUBLE FROM read_parquet('{parquet_sql}')"
    ).fetchone()

    connection.execute(
        f"""
        ATTACH 'ducklake:{catalog_sql}' AS lake
        (DATA_PATH '{data_sql}')
        """
    )
    connection.execute(
        f"""
        CREATE TABLE lake.measurements AS
        SELECT * FROM read_csv_auto('{csv_sql}', header = true)
        """
    )
    ducklake_result = connection.execute(
        "SELECT count(*)::BIGINT, sum(value)::DOUBLE FROM lake.measurements"
    ).fetchone()
    snapshot_before_update = connection.execute(
        "SELECT max(snapshot_id) FROM lake.snapshots()"
    ).fetchone()[0]
    snapshot_count_before_update = connection.execute(
        "SELECT count(*) FROM lake.snapshots()"
    ).fetchone()[0]

    connection.execute(
        "UPDATE lake.measurements SET value = 999.0 WHERE sample_id = 'S00001'"
    )
    current_value = connection.execute(
        "SELECT value FROM lake.measurements WHERE sample_id = 'S00001'"
    ).fetchone()[0]
    historical_value = connection.execute(
        f"""
        SELECT value
        FROM lake.measurements AT (VERSION => {snapshot_before_update})
        WHERE sample_id = 'S00001'
        """
    ).fetchone()[0]
    snapshot_count_after_update = connection.execute(
        "SELECT count(*) FROM lake.snapshots()"
    ).fetchone()[0]
    extension_version = connection.execute(
        """
        SELECT extension_version
        FROM duckdb_extensions()
        WHERE extension_name = 'ducklake'
        """
    ).fetchone()[0]
    connection.close()

    reopened = duckdb.connect()
    reopened.execute("LOAD ducklake")
    reopened.execute(
        f"""
        ATTACH 'ducklake:{catalog_sql}' AS lake
        (DATA_PATH '{data_sql}')
        """
    )
    reopened_result = reopened.execute(
        """
        SELECT count(*)::BIGINT, value::DOUBLE
        FROM lake.measurements
        WHERE sample_id = 'S00001'
        GROUP BY value
        """
    ).fetchone()
    reopened.close()

    assert raw_result == parquet_result == ducklake_result == (600, 180300.0)
    assert current_value == 999.0
    assert historical_value == 1.0
    assert snapshot_count_after_update > snapshot_count_before_update
    assert reopened_result == (1, 999.0)
    assert catalog_path.exists()
    assert list(data_path.rglob("*.parquet"))

    print(
        json.dumps(
            {
                "duckdb_version": duckdb.__version__,
                "ducklake_extension_version": extension_version,
                "baseline_results_equal": True,
                "baseline_result": list(raw_result),
                "snapshot_count_before_update": snapshot_count_before_update,
                "snapshot_count_after_update": snapshot_count_after_update,
                "historical_value": historical_value,
                "current_value": current_value,
                "reopened_row_and_value": list(reopened_result),
                "catalog_created": catalog_path.exists(),
                "parquet_files_created": len(list(data_path.rglob("*.parquet"))),
                "storage": "local temporary directory; no S3 access used",
            },
            indent=2,
            sort_keys=True,
        )
    )
