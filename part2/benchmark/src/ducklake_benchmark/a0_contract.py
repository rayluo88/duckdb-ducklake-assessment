from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path
import re
from typing import Literal
from typing import Mapping

import duckdb


ControlProvenance = Literal["observed", "historically_proven", "reproduced_inferred"]

REQUIRED_WRITER_CONTROLS = (
    "writer_threads",
    "compression",
    "row_group_size",
    "parquet_version",
    "string_dictionary_page_size_limit",
    "preserve_insertion_order",
    "per_thread_output",
    "write_partition_columns",
    "append",
    "overwrite",
)


@dataclass(frozen=True)
class HistoricalControl:
    value: object
    provenance: ControlProvenance

    def __post_init__(self) -> None:
        if self.provenance not in {
            "observed",
            "historically_proven",
            "reproduced_inferred",
        }:
            raise ValueError(f"unsupported control provenance: {self.provenance}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_identity(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return {"bytes": path.stat().st_size, "sha256": _sha256(path)}


def _rows_as_dicts(
    connection: duckdb.DuckDBPyConnection, query: str, parameters: list[object] | None = None
) -> list[dict[str, object]]:
    cursor = connection.execute(query, parameters)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _observed_controls(
    *, parquet_file_metadata: dict[str, object], parquet_rows: list[dict[str, object]]
) -> dict[str, object]:
    row_group_sizes = sorted(
        {
            row["row_group_num_rows"]
            for row in parquet_rows
            if row["row_group_num_rows"] is not None
        }
    )
    codecs = sorted({row["compression"] for row in parquet_rows if row["compression"] is not None})
    dictionary_pages = sum(
        row["dictionary_page_offset"] is not None for row in parquet_rows
    )
    return {
        "writer_threads": "not_observable_from_parquet",
        "compression": codecs,
        "row_group_size": {"observed_row_group_rows": row_group_sizes},
        "parquet_version": parquet_file_metadata["format_version"],
        "string_dictionary_page_size_limit": {
            "dictionary_page_count": dictionary_pages,
            "limit": "not_observable_from_parquet",
        },
        "preserve_insertion_order": "not_observable_from_parquet",
        "per_thread_output": "not_observable_from_parquet",
        "write_partition_columns": "not_observable_from_parquet",
        "append": "not_observable_from_parquet",
        "overwrite": "not_observable_from_parquet",
    }


def build_a0_contract(
    *,
    database_path: Path,
    a0_path: Path,
    historical_controls: Mapping[str, HistoricalControl],
) -> dict[str, object]:
    """Inventory immutable A0 and reject any writer input that cannot be historically proven."""

    database_path = database_path.resolve()
    a0_path = a0_path.resolve()
    unknown_controls = set(historical_controls).difference(REQUIRED_WRITER_CONTROLS)
    if unknown_controls:
        raise ValueError(f"unknown writer controls: {sorted(unknown_controls)}")

    started_monotonic_ns = time.monotonic_ns()
    with duckdb.connect(str(database_path), read_only=True) as connection:
        schema = [
            {"name": row[0], "type": row[1], "nullable": row[2] == "YES"}
            for row in connection.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(a0_path)]).fetchall()
        ]
        parquet_rows = _rows_as_dicts(connection, "SELECT * FROM parquet_metadata(?)", [str(a0_path)])
        parquet_file_metadata_rows = _rows_as_dicts(
            connection, "SELECT * FROM parquet_file_metadata(?)", [str(a0_path)]
        )
        if len(parquet_file_metadata_rows) != 1:
            raise ValueError("A0 contract requires exactly one physical Parquet file")
        parquet_file_metadata = parquet_file_metadata_rows[0]
        row_count = connection.execute("SELECT count(*) FROM read_parquet(?)", [str(a0_path)]).fetchone()[0]
        settings = _rows_as_dicts(connection, "SELECT * FROM duckdb_settings() ORDER BY name")
        extensions = _rows_as_dicts(connection, "SELECT * FROM duckdb_extensions() ORDER BY extension_name")
        duckdb_version = connection.execute("SELECT version()").fetchone()[0]

    observed_controls = _observed_controls(
        parquet_file_metadata=parquet_file_metadata, parquet_rows=parquet_rows
    )
    controls: dict[str, dict[str, object]] = {}
    for name in REQUIRED_WRITER_CONTROLS:
        supplied = historical_controls.get(name)
        if supplied is None:
            controls[name] = {"value": observed_controls[name], "provenance": "observed"}
        else:
            controls[name] = {"value": supplied.value, "provenance": supplied.provenance}

    amendment_required_for = [
        name for name in REQUIRED_WRITER_CONTROLS if controls[name]["provenance"] != "historically_proven"
    ]
    disk = shutil.disk_usage(a0_path.parent)
    completed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "contract_version": 1,
        "valid": not amendment_required_for,
        "requires_design_amendment": bool(amendment_required_for),
        "amendment_required_for": amendment_required_for,
        "amendment_reason": (
            "all frozen writer controls must be historically_proven before one-factor isolation"
            if amendment_required_for
            else None
        ),
        "source": {"database": _file_identity(database_path), "a0": _file_identity(a0_path)},
        "source_paths": {"database": str(database_path), "a0": str(a0_path)},
        "schema": schema,
        "parquet": {
            "file_count": 1,
            "row_count": row_count,
            "row_group_count": parquet_file_metadata["num_row_groups"],
            "format_version": parquet_file_metadata["format_version"],
            "created_by": parquet_file_metadata["created_by"],
            "codecs": sorted(
                {row["compression"] for row in parquet_rows if row["compression"] is not None}
            ),
            "dictionary_page_count": sum(
                row["dictionary_page_offset"] is not None for row in parquet_rows
            ),
        },
        "controls": controls,
        "environment": {
            "duckdb_version": duckdb_version,
            "duckdb_settings": settings,
            "duckdb_extensions": extensions,
            "python_version": sys.version,
            "python_implementation": platform.python_implementation(),
            "duckdb_package_version": importlib.metadata.version("duckdb"),
            "os": platform.platform(),
            "cpu_count": os.cpu_count(),
            "disk": {"total": disk.total, "used": disk.used, "free": disk.free},
        },
        "timestamps": {
            "completed_at_utc": completed_at,
            "started_monotonic_ns": started_monotonic_ns,
            "completed_monotonic_ns": time.monotonic_ns(),
        },
    }


def write_a0_contract(
    *,
    database_path: Path,
    a0_path: Path,
    historical_controls: Mapping[str, HistoricalControl],
    output_path: Path,
) -> dict[str, object]:
    output_path = output_path.resolve()
    if output_path.exists():
        raise FileExistsError(f"A0 contract output already exists: {output_path}")
    contract = build_a0_contract(
        database_path=database_path,
        a0_path=a0_path,
        historical_controls=historical_controls,
    )
    output_path.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n")
    return contract


def _require_historically_proven(
    historical_controls: Mapping[str, HistoricalControl]
) -> dict[str, HistoricalControl]:
    missing = set(REQUIRED_WRITER_CONTROLS).difference(historical_controls)
    if missing:
        raise ValueError(f"missing historically proven controls: {sorted(missing)}")
    unproven = [
        name
        for name in REQUIRED_WRITER_CONTROLS
        if historical_controls[name].provenance != "historically_proven"
    ]
    if unproven:
        raise ValueError(f"unproven writer controls: {unproven}")
    return {name: historical_controls[name] for name in REQUIRED_WRITER_CONTROLS}


def _parquet_summary(connection: duckdb.DuckDBPyConnection, path: Path) -> dict[str, object]:
    metadata = _rows_as_dicts(connection, "SELECT * FROM parquet_file_metadata(?)", [str(path)])
    if len(metadata) != 1:
        raise ValueError(f"expected one Parquet file: {path}")
    row_groups = connection.execute(
        """
        SELECT row_group_num_rows
        FROM (
            SELECT DISTINCT row_group_id, row_group_num_rows
            FROM parquet_metadata(?)
        )
        ORDER BY row_group_id
        """,
        [str(path)],
    ).fetchall()
    codecs = connection.execute(
        "SELECT DISTINCT compression FROM parquet_metadata(?) ORDER BY compression", [str(path)]
    ).fetchall()
    return {
        "file_count": 1,
        "format_version": metadata[0]["format_version"],
        "row_group_rows": [row[0] for row in row_groups],
        "codecs": [row[0] for row in codecs],
    }


def reproduce_a0_reference(
    *,
    database_path: Path,
    source_table: str,
    a0_path: Path,
    output_path: Path,
    historical_controls: Mapping[str, HistoricalControl],
) -> dict[str, object]:
    """Write an explicit-control A0 reference and compare it without changing historical A0."""

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", source_table):
        raise ValueError(f"source_table must be a safe SQL identifier: {source_table!r}")
    output_path = output_path.resolve()
    if output_path.exists():
        raise FileExistsError(f"A0 reference output already exists: {output_path}")
    controls = _require_historically_proven(historical_controls)
    compression = str(controls["compression"].value).lower()
    if compression not in {"snappy", "zstd", "gzip", "lz4", "uncompressed"}:
        raise ValueError(f"unsupported Parquet compression: {compression!r}")
    parquet_version = int(controls["parquet_version"].value)
    if parquet_version not in {1, 2}:
        raise ValueError("parquet_version must be 1 or 2")
    row_group_size = int(controls["row_group_size"].value)
    dictionary_page_size_limit = int(controls["string_dictionary_page_size_limit"].value)
    writer_threads = int(controls["writer_threads"].value)
    if min(row_group_size, dictionary_page_size_limit, writer_threads) <= 0:
        raise ValueError("writer thread, row-group and dictionary-page limits must be positive")
    if controls["per_thread_output"].value is not False:
        raise ValueError("A0 reproduction requires per_thread_output=false")
    if controls["write_partition_columns"].value is not False:
        raise ValueError("A0 reproduction requires write_partition_columns=false")
    if controls["append"].value is not False or controls["overwrite"].value is not False:
        raise ValueError("A0 reproduction requires append=false and overwrite=false")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(database_path.resolve()), read_only=True) as connection:
        connection.execute(f"SET threads = {writer_threads}")
        connection.execute(
            f"SET preserve_insertion_order = {'true' if controls['preserve_insertion_order'].value else 'false'}"
        )
        connection.execute(
            f"""
            COPY (SELECT * FROM {source_table}) TO ?
            (
                FORMAT PARQUET,
                COMPRESSION {compression},
                ROW_GROUP_SIZE {row_group_size},
                PARQUET_VERSION v{parquet_version},
                STRING_DICTIONARY_PAGE_SIZE_LIMIT {dictionary_page_size_limit},
                PER_THREAD_OUTPUT false,
                WRITE_PARTITION_COLUMNS false
            )
            """,
            [str(output_path)],
        )
        left_count, right_count, difference_rows = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM read_parquet(?)),
                (SELECT count(*) FROM read_parquet(?)),
                (
                    SELECT count(*)
                    FROM (
                        (SELECT * FROM read_parquet(?) EXCEPT ALL SELECT * FROM read_parquet(?))
                        UNION ALL
                        (SELECT * FROM read_parquet(?) EXCEPT ALL SELECT * FROM read_parquet(?))
                    ) AS differences
                )
            """,
            [str(a0_path.resolve()), str(output_path), str(a0_path.resolve()), str(output_path), str(output_path), str(a0_path.resolve())],
        ).fetchone()
        a0_summary = _parquet_summary(connection, a0_path.resolve())
        reference_summary = _parquet_summary(connection, output_path)

    causal_attributes = {
        "file_count_match": a0_summary["file_count"] == reference_summary["file_count"],
        "format_version_match": a0_summary["format_version"] == reference_summary["format_version"],
        "codecs_match": a0_summary["codecs"] == reference_summary["codecs"],
        "row_group_rows_match": a0_summary["row_group_rows"] == reference_summary["row_group_rows"],
    }
    logical_equivalence = {
        "valid": left_count == right_count and difference_rows == 0,
        "a0_row_count": left_count,
        "reference_row_count": right_count,
        "difference_rows": difference_rows,
    }
    return {
        "valid": logical_equivalence["valid"] and all(causal_attributes.values()),
        "reference": _file_identity(output_path),
        "logical_equivalence": logical_equivalence,
        "causal_attributes": causal_attributes,
        "a0_parquet": a0_summary,
        "reference_parquet": reference_summary,
        "byte_identical": _sha256(a0_path.resolve()) == _sha256(output_path),
    }
