import json
from dataclasses import dataclass
from pathlib import Path

import duckdb

from .model import Scenario
from .model import SyntheticProfile


@dataclass(frozen=True)
class FixtureArtifacts:
    database_path: Path
    parquet_path: Path
    manifest_path: Path


def build_local_fixture(output_dir: Path, profile: SyntheticProfile) -> FixtureArtifacts:
    output_dir.mkdir(parents=True, exist_ok=True)
    database_path = output_dir / "benchmark.duckdb"
    parquet_path = output_dir / "a0" / "data.parquet"
    manifest_path = output_dir / "fixture-manifest.json"
    parquet_path.parent.mkdir(parents=True, exist_ok=True)

    if database_path.exists() or parquet_path.exists() or manifest_path.exists():
        raise FileExistsError(f"fixture output already exists in {output_dir}")

    if profile.scenario is Scenario.SHUFFLED:
        filter_key_sql = f"CAST(hash(row_id + {profile.seed}) % 1000 AS INTEGER)"
    elif profile.scenario is Scenario.MODERATELY_CORRELATED:
        filter_key_sql = (
            "CAST((floor(row_id * 1000.0 / "
            f"{profile.rows}) + hash(row_id + {profile.seed}) % 50) % 1000 AS INTEGER)"
        )
    else:
        filter_key_sql = f"CAST(floor(row_id * 1000.0 / {profile.rows}) AS INTEGER)"

    escaped_parquet_path = str(parquet_path).replace("'", "''")
    with duckdb.connect(str(database_path)) as connection:
        connection.execute(
            f"""
            CREATE TABLE synthetic_data AS
            SELECT
                row_id,
                CAST(row_id % greatest(1, floor({profile.rows} / 10)) AS BIGINT) AS subject_id,
                DATE '2020-01-01' + CAST(row_id % 365 AS INTEGER) AS event_date,
                concat('C', CAST(row_id % 4 AS VARCHAR)) AS cohort,
                {filter_key_sql} AS filter_key,
                CAST(hash(row_id * 17 + {profile.seed}) % 1000 AS INTEGER) AS nonaligned_key,
                CAST((row_id % 100000) / 100.0 AS DECIMAL(18, 2)) AS measure,
                CAST(row_id % 1000 AS DOUBLE) / 7.0 AS floating_measure,
                concat('V', CAST(row_id % 100 AS VARCHAR)) AS text_value,
                CASE WHEN row_id % 10 = 0 THEN NULL ELSE CAST(row_id % 50 AS INTEGER) END
                    AS nullable_value
            FROM range({profile.rows}) AS generated(row_id)
            ORDER BY row_id
            """
        )
        connection.execute(
            f"COPY synthetic_data TO '{escaped_parquet_path}' (FORMAT PARQUET)"
        )
        duckdb_version = connection.execute("SELECT version()").fetchone()[0]
        schema = [
            {"name": row[0], "type": row[1], "nullable": row[2] == "YES"}
            for row in connection.execute("DESCRIBE synthetic_data").fetchall()
        ]
        row_group_count, parquet_row_count = connection.execute(
            """
            SELECT count(*), sum(row_group_num_rows)
            FROM (
                SELECT DISTINCT file_name, row_group_id, row_group_num_rows
                FROM parquet_metadata(?)
            )
            """,
            [str(parquet_path)],
        ).fetchone()

    manifest = {
        "manifest_version": 1,
        "profile": profile.to_manifest(),
        "environment": {"duckdb_version": duckdb_version},
        "schema": schema,
        "representations": {
            "A0": {
                "kind": "duckdb_default_write_parquet",
                "path": str(parquet_path.relative_to(output_dir)),
                "provenance": "assumed",
            },
            "D": {
                "kind": "native_duckdb_table",
                "path": str(database_path.relative_to(output_dir)),
                "table": "synthetic_data",
                "provenance": "assumed",
            },
        },
        "parquet": {
            "file_count": 1,
            "physical_bytes": parquet_path.stat().st_size,
            "row_group_count": row_group_count,
            "row_count": parquet_row_count,
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    return FixtureArtifacts(
        database_path=database_path,
        parquet_path=parquet_path,
        manifest_path=manifest_path,
    )
