from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import statistics
import sys
import time
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class WriterControls:
    compression: str
    compression_level: int | None
    row_group_size: int
    parquet_version: int
    dictionary_page_size_limit: int
    preserve_insertion_order: bool
    per_thread_output: bool
    filename_pattern: str | None
    write_partition_columns: bool
    append: bool
    overwrite: bool
    writer_threads: int


@dataclass(frozen=True)
class LayoutSpec:
    layout_id: str
    provenance: str
    order_by: tuple[str, ...]
    file_size_bytes: str | None
    controls: WriterControls

    @property
    def compression(self) -> str:
        return self.controls.compression

    @property
    def compression_level(self) -> int | None:
        return self.controls.compression_level

    @property
    def row_group_size(self) -> int:
        return self.controls.row_group_size

    @property
    def parquet_version(self) -> int:
        return self.controls.parquet_version

    @property
    def dictionary_page_size_limit(self) -> int:
        return self.controls.dictionary_page_size_limit

    @property
    def per_thread_output(self) -> bool:
        return self.controls.per_thread_output

    @property
    def filename_pattern(self) -> str | None:
        return self.controls.filename_pattern

    @property
    def append(self) -> bool:
        return self.controls.append

    @property
    def overwrite(self) -> bool:
        return self.controls.overwrite

    @property
    def writer_threads(self) -> int:
        return self.controls.writer_threads


def _controls(*, compression: str, compression_level: int | None = None, per_thread_output: bool = False, filename_pattern: str | None = None) -> WriterControls:
    return WriterControls(
        compression=compression,
        compression_level=compression_level,
        row_group_size=122_880,
        parquet_version=1,
        dictionary_page_size_limit=1_048_576,
        preserve_insertion_order=True,
        per_thread_output=per_thread_output,
        filename_pattern=filename_pattern,
        write_partition_columns=False,
        append=False,
        overwrite=False,
        writer_threads=1,
    )


def layout_specs() -> tuple[LayoutSpec, ...]:
    """Return the only pre-approved layout factors for the focused screen."""

    return (
        LayoutSpec(
            layout_id="B1",
            provenance="assumed",
            order_by=("filter_key", "row_id"),
            file_size_bytes=None,
            controls=_controls(compression="snappy"),
        ),
        LayoutSpec(
            layout_id="B2",
            provenance="assumed",
            order_by=(),
            file_size_bytes="128MB",
            controls=_controls(
                compression="snappy", per_thread_output=True, filename_pattern="part_{i}"
            ),
        ),
        LayoutSpec(
            layout_id="B3",
            provenance="assumed",
            order_by=(),
            file_size_bytes=None,
            controls=_controls(compression="zstd", compression_level=3),
        ),
        LayoutSpec(
            layout_id="Bstar",
            provenance="approved_combination_20260809",
            order_by=("filter_key", "row_id"),
            file_size_bytes="128MB",
            controls=_controls(
                compression="snappy", per_thread_output=True, filename_pattern="part_{i}"
            ),
        ),
    )


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def render_writer_sql(spec: LayoutSpec, *, source_table: str, output_path: Path) -> str:
    """Render the single approved DuckDB COPY statement for one frozen layout."""

    if spec not in layout_specs():
        raise ValueError(f"layout is not in the frozen screen: {spec.layout_id}")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", source_table):
        raise ValueError(f"source_table must be a safe SQL identifier: {source_table!r}")
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(f"layout output already exists: {output_path}")

    source_query = f"SELECT * FROM {source_table}"
    if spec.order_by:
        source_query += " ORDER BY " + ", ".join(spec.order_by)
    copy_options = [
        "FORMAT PARQUET",
        f"COMPRESSION {spec.compression}",
        f"ROW_GROUP_SIZE {spec.row_group_size}",
        f"PARQUET_VERSION v{spec.parquet_version}",
        f"STRING_DICTIONARY_PAGE_SIZE_LIMIT {spec.dictionary_page_size_limit}",
        f"PER_THREAD_OUTPUT {'true' if spec.per_thread_output else 'false'}",
        "WRITE_PARTITION_COLUMNS false",
    ]
    if spec.compression_level is not None:
        copy_options.append(f"COMPRESSION_LEVEL {spec.compression_level}")
    if spec.file_size_bytes is not None:
        copy_options.append(f"FILE_SIZE_BYTES {_sql_literal(spec.file_size_bytes)}")
    if spec.filename_pattern is not None:
        copy_options.append(f"FILENAME_PATTERN {_sql_literal(spec.filename_pattern)}")

    return (
        "COPY (\n"
        f"    {source_query}\n"
        f") TO {_sql_literal(str(output_path))} (\n"
        + ",\n".join(f"    {option}" for option in copy_options)
        + "\n)"
    )


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


def _writer_index(path: Path) -> int:
    match = re.fullmatch(r"part_(\d+)(?:\.parquet)?", path.name)
    if match is None:
        raise ValueError(f"B2 output file does not use the frozen writer pattern: {path.name}")
    return int(match.group(1))


def _inventory_file(
    connection: duckdb.DuckDBPyConnection, path: Path, output_dir: Path, writer_index: int | None
) -> dict[str, object]:
    metadata_rows = _rows_as_dicts(
        connection, "SELECT * FROM parquet_file_metadata(?)", [str(path)]
    )
    if len(metadata_rows) != 1:
        raise ValueError(f"expected one metadata row for {path}")
    row_groups = connection.execute(
        """
        SELECT row_group_id, row_group_num_rows
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
        "path": path.relative_to(output_dir).as_posix(),
        "writer_index": writer_index,
        **_file_identity(path),
        "row_groups": [{"id": row[0], "rows": row[1]} for row in row_groups],
        "codecs": [row[0] for row in codecs],
        "format_version": metadata_rows[0]["format_version"],
    }


def evaluate_b2_campaign_size_gate(files: list[dict[str, object]]) -> dict[str, object]:
    """Apply the predeclared 128 MiB B2 acceptance gate to an observed file inventory."""

    mib = 1_048_576
    ordered = sorted(files, key=lambda file: int(file["writer_index"]))
    reasons: list[str] = []
    if not 6 <= len(ordered) <= 10:
        reasons.append("file_count")
    non_final_bytes = [int(file["bytes"]) for file in ordered[:-1]]
    if not non_final_bytes or any(not 96 * mib <= size <= 160 * mib for size in non_final_bytes):
        reasons.append("non_final_size")
    elif not 112 * mib <= statistics.median(non_final_bytes) <= 144 * mib:
        reasons.append("median_non_final_size")
    if not ordered or not 0 < int(ordered[-1]["bytes"]) <= 160 * mib:
        reasons.append("final_remainder")
    if reasons:
        return {"evaluated": True, "status": "unsupported", "reasons": reasons}
    return {"evaluated": True, "status": "accepted"}


def _current_settings(connection: duckdb.DuckDBPyConnection) -> dict[str, str]:
    return {
        row[0]: row[1]
        for row in connection.execute("SELECT name, value FROM duckdb_settings()").fetchall()
    }


def generate_layout(
    spec: LayoutSpec,
    *,
    database_path: Path,
    a0_path: Path,
    source_table: str,
    output_dir: Path,
    enforce_campaign_scale: bool = False,
) -> dict[str, object]:
    """Generate one frozen layout from a read-only native source and write its immutable manifest."""

    if spec not in layout_specs():
        raise ValueError(f"layout is not in the frozen screen: {spec.layout_id}")
    database_path = database_path.resolve()
    a0_path = a0_path.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError(f"layout output directory already exists: {output_dir}")
    if not database_path.is_file() or not a0_path.is_file():
        raise FileNotFoundError("database_path and a0_path must be existing files")

    output_dir.mkdir(parents=True)
    data_path = output_dir / ("data" if spec.per_thread_output else "data.parquet")
    sql = render_writer_sql(spec, source_table=source_table, output_path=data_path)
    started_monotonic_ns = time.monotonic_ns()
    started_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with duckdb.connect(str(database_path), read_only=True) as connection:
        connection.execute(f"SET threads = {spec.writer_threads}")
        connection.execute(
            f"SET preserve_insertion_order = {'true' if spec.controls.preserve_insertion_order else 'false'}"
        )
        writer_settings = _current_settings(connection)
        extensions = _rows_as_dicts(connection, "SELECT * FROM duckdb_extensions() ORDER BY extension_name")
        duckdb_version = connection.execute("SELECT version()").fetchone()[0]
        connection.execute(sql)
    completed_monotonic_ns = time.monotonic_ns()

    files = list(data_path.rglob("*.parquet")) if spec.per_thread_output else [data_path]
    if not files or any(not path.is_file() for path in files):
        raise ValueError(f"DuckDB did not create the expected Parquet output for {spec.layout_id}")
    if spec.per_thread_output:
        indexed_files = sorted(((_writer_index(path), path) for path in files), key=lambda item: item[0])
        if len({index for index, _ in indexed_files}) != len(indexed_files):
            raise ValueError("B2 writer indices must be unique")
    else:
        indexed_files = [(None, files[0])]

    with duckdb.connect() as inventory_connection:
        file_inventory = [
            _inventory_file(inventory_connection, path, output_dir, index)
            for index, path in indexed_files
        ]
    campaign_size_gate = (
        evaluate_b2_campaign_size_gate(file_inventory)
        if spec.layout_id in {"B2", "Bstar"} and enforce_campaign_scale
        else {"evaluated": False, "status": "not_evaluated"}
    )
    manifest = {
        "manifest_version": 1,
        "layout_id": spec.layout_id,
        "provenance": spec.provenance,
        "source": {"database": _file_identity(database_path), "a0": _file_identity(a0_path)},
        "source_paths": {"database": str(database_path), "a0": str(a0_path)},
        "source_table": source_table,
        "requested": asdict(spec),
        "writer": {
            "sql": sql,
            "settings": {
                "threads": writer_settings["threads"],
                "preserve_insertion_order": writer_settings["preserve_insertion_order"],
            },
        },
        "environment": {
            "duckdb_version": duckdb_version,
            "python_version": sys.version,
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "extensions": extensions,
        },
        "generation": {
            "started_at_utc": started_at_utc,
            "completed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "duration_ns": completed_monotonic_ns - started_monotonic_ns,
        },
        "files": file_inventory,
        "campaign_size_gate": campaign_size_gate,
        "status": "unsupported" if campaign_size_gate["status"] == "unsupported" else "generated",
    }
    (output_dir / "layout-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest
