from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Mapping

import duckdb

from .correctness import CorrectnessMismatch
from .correctness import validate_relations
from .layouts import layout_specs
from .queries import run_query_suite


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity_matches(path: Path, expected: Mapping[str, object]) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == expected.get("bytes")
        and _sha256(path) == expected.get("sha256")
    )


def _safe_identifier(value: str, *, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"{label} must be a safe SQL identifier: {value!r}")
    return value


def render_read_parquet(paths: list[Path]) -> str:
    if not paths:
        raise ValueError("at least one Parquet path is required")
    quoted = ", ".join("'" + str(path).replace("'", "''") + "'" for path in paths)
    return f"read_parquet([{quoted}], hive_partitioning=false)"


def _resolve_manifest_files(manifest: Mapping[str, object], representation_root: Path) -> list[Path]:
    root = representation_root.resolve()
    file_records = manifest.get("files")
    if not isinstance(file_records, list) or not file_records:
        raise ValueError("manifest must declare at least one file")
    resolved_files: list[Path] = []
    for record in file_records:
        if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
            raise ValueError("manifest file record must include a path")
        relative = Path(record["path"])
        if relative.is_absolute():
            raise ValueError("manifest path must remain below representation root")
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise ValueError("manifest path must remain below representation root") from error
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError(f"manifest file is missing or symlinked: {relative}")
        if candidate.stat().st_size != record.get("bytes") or _sha256(candidate) != record.get("sha256"):
            raise ValueError(f"manifest file identity mismatch: {relative}")
        resolved_files.append(candidate)
    if len(set(resolved_files)) != len(resolved_files):
        raise ValueError("manifest declares a duplicate Parquet file")
    observed = {path.resolve() for path in root.rglob("*.parquet") if not path.is_symlink()}
    if set(resolved_files) != observed:
        raise ValueError("manifest and filesystem Parquet inventories differ")
    return resolved_files


def _null_counts(connection: duckdb.DuckDBPyConnection, relation: str, columns: list[str]) -> dict[str, int]:
    select_list = ", ".join(
        f"count(*) FILTER (WHERE {column} IS NULL) AS {column}" for column in columns
    )
    row = connection.execute(f"SELECT {select_list} FROM {relation}").fetchone()
    return dict(zip(columns, row, strict=True))


def _fingerprints(
    connection: duckdb.DuckDBPyConnection, relation: str, columns: list[str]
) -> dict[str, int | None]:
    expression = ", ".join(columns)
    fingerprints: dict[str, int | None] = {}
    for threads in (1, 4, 8):
        connection.execute(f"SET threads = {threads}")
        fingerprints[str(threads)] = connection.execute(
            f"SELECT bit_xor(hash({expression})) FROM {relation}"
        ).fetchone()[0]
    return fingerprints


def _physical_order_failure_count(
    connection: duckdb.DuckDBPyConnection, relation_sql: str, layout_id: str
) -> int:
    if layout_id in {"B1", "Bstar"}:
        return connection.execute(
            f"""
            WITH ordered AS (
                SELECT
                    filter_key,
                    row_id,
                    lag(filter_key) OVER () AS previous_filter_key,
                    lag(row_id) OVER () AS previous_row_id
                FROM {relation_sql}
            )
            SELECT count(*)
            FROM ordered
            WHERE filter_key < previous_filter_key
               OR (filter_key = previous_filter_key AND row_id < previous_row_id)
            """
        ).fetchone()[0]
    return connection.execute(
        f"""
        WITH ordered AS (
            SELECT row_id, lag(row_id) OVER () AS previous_row_id
            FROM {relation_sql}
        )
        SELECT count(*) FROM ordered WHERE row_id <= previous_row_id
        """
    ).fetchone()[0]


def validate_layout(
    *,
    manifest: Mapping[str, object],
    representation_root: Path,
    database_path: Path,
    source_table: str,
) -> dict[str, object]:
    """Run untimed source, value, order and declared-factor gates for one generated layout."""

    source_table = _safe_identifier(source_table, label="source_table")
    layout_id = manifest.get("layout_id")
    known_specs = {spec.layout_id: spec for spec in layout_specs()}
    if layout_id not in known_specs:
        raise ValueError(f"unknown frozen layout: {layout_id!r}")
    resolved_files = _resolve_manifest_files(manifest, representation_root)
    source = manifest.get("source")
    if not isinstance(source, Mapping):
        return {"valid": False, "status": "invalid_correctness", "failures": ["source_manifest"]}
    database_path = database_path.resolve()
    source_paths = manifest.get("source_paths")
    declared_a0 = source_paths.get("a0") if isinstance(source_paths, Mapping) else None
    a0_path = (
        Path(declared_a0).resolve()
        if isinstance(declared_a0, str)
        else database_path.parent / "a0" / "data.parquet"
    )
    failures = []
    if not _identity_matches(database_path, source.get("database", {})):
        failures.append("source_database_identity")
    if not _identity_matches(a0_path, source.get("a0", {})):
        failures.append("source_a0_identity")
    if failures:
        return {"valid": False, "status": "invalid_correctness", "failures": failures}

    relation_sql = render_read_parquet(resolved_files)
    with duckdb.connect(str(database_path), read_only=True) as connection:
        connection.execute(f"CREATE TEMP VIEW variant AS SELECT * FROM {relation_sql}")
        source_schema = connection.execute(f"DESCRIBE {source_table}").fetchall()
        variant_schema = connection.execute("DESCRIBE variant").fetchall()
        source_columns = [row[0] for row in source_schema]
        schema_match = source_schema == variant_schema
        source_null_counts = _null_counts(connection, source_table, source_columns)
        variant_null_counts = _null_counts(connection, "variant", source_columns)
        try:
            value_equivalence = validate_relations(connection, source_table, "variant")
            value_equivalence["valid"] = True
        except CorrectnessMismatch as error:
            value_equivalence = {"valid": False, "error": str(error)}
        query_results_match = run_query_suite(connection, source_table) == run_query_suite(
            connection, "variant"
        )
        source_fingerprints = _fingerprints(connection, source_table, source_columns)
        variant_fingerprints = _fingerprints(connection, "variant", source_columns)
        order_failures = _physical_order_failure_count(connection, relation_sql, str(layout_id))

    expected_codec = "ZSTD" if layout_id == "B3" else "SNAPPY"
    codecs_match = all(file.get("codecs") == [expected_codec] for file in manifest["files"])
    causal_isolation = {
        "valid": order_failures == 0 and codecs_match,
        "order_failure_rows": order_failures,
        "expected_codec": expected_codec,
        "codecs_match": codecs_match,
    }
    valid = (
        schema_match
        and source_null_counts == variant_null_counts
        and value_equivalence["valid"]
        and query_results_match
        and source_fingerprints == variant_fingerprints
        and causal_isolation["valid"]
    )
    return {
        "valid": valid,
        "status": "validated" if valid else "invalid_correctness",
        "resolved_files": [str(path) for path in resolved_files],
        "correctness": {
            "schema_match": schema_match,
            "source_null_counts": source_null_counts,
            "variant_null_counts": variant_null_counts,
            "value_equivalence": value_equivalence,
            "query_results_match": query_results_match,
            "fingerprints": {
                "source": source_fingerprints,
                "variant": variant_fingerprints,
                "evidence": "probabilistic_support_only",
            },
        },
        "causal_isolation": causal_isolation,
    }
