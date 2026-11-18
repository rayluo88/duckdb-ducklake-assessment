"""Controlled DuckLake C1 catalogue-over-identical-Parquet contract.

C1 deliberately registers a fresh, byte-identical copy of the approved B1 object.  It measures the
increment of DuckLake catalogue management, not a different Parquet writer or physical layout.
"""

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import duckdb

from .correctness import CorrectnessMismatch, validate_relations
from .gcs import (
    GcsObject,
    configure_duckdb_gcs_oauth,
    gcs_media_url_for_generation,
    render_gcs_read_parquet,
)
from .queries import run_query_suite


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_C1_MODE = "registered_byte_identical_copy"
_C1_COMPARISONS = {"a0_representation", "direct_clone_increment"}


def _object(value: object, *, label: str) -> GcsObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    try:
        object_identity = GcsObject(**value)
    except TypeError as error:
        raise ValueError(f"{label} is missing an object identity field") from error
    if (
        not object_identity.object
        or not object_identity.generation
        or len(object_identity.sha256) != 64
        or object_identity.physical_bytes <= 0
        or not object_identity.crc32c
    ):
        raise ValueError(f"{label} has an invalid immutable GCS object identity")
    return object_identity


def _identifier(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a simple SQL identifier")
    return value


@dataclass(frozen=True)
class DuckLakeC1Candidate:
    layout_id: str
    management_mode: str
    object: GcsObject


@dataclass(frozen=True)
class DuckLakeC1Catalog:
    metadata_path: Path
    metadata_sha256: str
    catalog_alias: str
    schema: str
    table: str

    @property
    def relation_sql(self) -> str:
        return f"{self.catalog_alias}.{self.schema}.{self.table}"


@dataclass(frozen=True)
class DuckLakeC1Manifest:
    stage_id: str
    bucket: str
    bucket_region: str
    source_b1: GcsObject
    a0_lane_1: GcsObject
    a0_lane_2: GcsObject
    candidate: DuckLakeC1Candidate
    catalog: DuckLakeC1Catalog


@dataclass(frozen=True)
class DuckLakeC1Plan:
    stage_id: str
    bucket: str
    bucket_region: str
    source_b1_stage_id: str
    source_b1: GcsObject
    a0_lane_1: GcsObject
    a0_lane_2: GcsObject
    candidate: DuckLakeC1Candidate


def read_ducklake_c1_manifest(path: Path) -> DuckLakeC1Manifest:
    """Read and fail closed on a DuckLake C1 metadata-only comparison manifest."""

    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("DuckLake C1 manifest must be a JSON object")
    stage_id = raw.get("stage_id")
    bucket = raw.get("bucket")
    bucket_region = raw.get("bucket_region")
    if (
        not isinstance(stage_id, str)
        or not stage_id.startswith("P2-GCE-GCS-DUCKLAKE-C1-")
        or not isinstance(bucket, str)
        or not bucket
        or not isinstance(bucket_region, str)
        or not bucket_region
    ):
        raise ValueError("DuckLake C1 manifest has an invalid stage identity")

    source = raw.get("source_b1")
    if not isinstance(source, dict) or source.get("stage_id") != "P2-GCE-GCS-B1-1GB-20260809-001":
        raise ValueError("DuckLake C1 must identify the approved B1 source stage")
    source_b1 = _object(source.get("object"), label="source_b1.object")
    lane_1 = _object(raw.get("a0_lane_1"), label="a0_lane_1")
    lane_2 = _object(raw.get("a0_lane_2"), label="a0_lane_2")
    if (
        lane_1.object == lane_2.object
        or lane_1.generation == lane_2.generation
        or lane_1.sha256 != lane_2.sha256
        or lane_1.physical_bytes != lane_2.physical_bytes
    ):
        raise ValueError("DuckLake C1 A0 lanes must be distinct byte-identical objects")

    candidate_raw = raw.get("candidate")
    if not isinstance(candidate_raw, dict):
        raise ValueError("DuckLake C1 candidate must be an object")
    candidate = DuckLakeC1Candidate(
        layout_id=candidate_raw.get("layout_id"),
        management_mode=candidate_raw.get("management_mode"),
        object=_object(candidate_raw.get("object"), label="candidate.object"),
    )
    if candidate.layout_id != "C1" or candidate.management_mode != _C1_MODE:
        raise ValueError("DuckLake C1 manifest has an unapproved management mode")
    if candidate.object.object == source_b1.object or candidate.object.generation == source_b1.generation:
        raise ValueError("DuckLake C1 must use a fresh object, not the B1 baseline object")
    if (
        candidate.object.sha256 != source_b1.sha256
        or candidate.object.physical_bytes != source_b1.physical_bytes
        or candidate.object.crc32c != source_b1.crc32c
    ):
        raise ValueError("DuckLake C1 candidate must be byte-identical to the B1 source")

    catalog_raw = raw.get("catalog")
    if not isinstance(catalog_raw, dict):
        raise ValueError("DuckLake C1 catalog must be an object")
    if catalog_raw.get("catalog_type") != "duckdb_local_single_client":
        raise ValueError("DuckLake C1 requires the approved local single-client catalog")
    if catalog_raw.get("read_only_during_timing") is not True:
        raise ValueError("DuckLake C1 timing requires a read-only catalog attachment")
    metadata_path_raw = catalog_raw.get("metadata_path")
    if not isinstance(metadata_path_raw, str):
        raise ValueError("DuckLake C1 catalog must provide a metadata path")
    metadata_path = Path(metadata_path_raw)
    if not metadata_path.is_absolute() or not metadata_path.is_file() or metadata_path.is_symlink():
        raise ValueError("DuckLake C1 metadata path must be an existing non-symlink absolute file")
    metadata_sha256 = catalog_raw.get("metadata_sha256")
    if not isinstance(metadata_sha256, str) or len(metadata_sha256) != 64:
        raise ValueError("DuckLake C1 catalog must provide a metadata checksum")
    if sha256(metadata_path.read_bytes()).hexdigest() != metadata_sha256:
        raise ValueError("DuckLake C1 metadata checksum does not match the catalog file")
    catalog = DuckLakeC1Catalog(
        metadata_path=metadata_path,
        metadata_sha256=metadata_sha256,
        catalog_alias=_identifier(catalog_raw.get("catalog_alias"), label="catalog.catalog_alias"),
        schema=_identifier(catalog_raw.get("schema"), label="catalog.schema"),
        table=_identifier(catalog_raw.get("table"), label="catalog.table"),
    )
    if catalog.relation_sql != "c1.main.b1":
        raise ValueError("DuckLake C1 requires the frozen c1.main.b1 read relation")
    return DuckLakeC1Manifest(
        stage_id=stage_id,
        bucket=bucket,
        bucket_region=bucket_region,
        source_b1=source_b1,
        a0_lane_1=lane_1,
        a0_lane_2=lane_2,
        candidate=candidate,
        catalog=catalog,
    )


def read_ducklake_c1_plan(path: Path) -> DuckLakeC1Plan:
    """Read the pre-catalogue C1 input and enforce the identical-object contract."""

    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("DuckLake C1 plan must be a JSON object")
    stage_id = raw.get("stage_id")
    bucket = raw.get("bucket")
    bucket_region = raw.get("bucket_region")
    if (
        not isinstance(stage_id, str)
        or not stage_id.startswith("P2-GCE-GCS-DUCKLAKE-C1-")
        or not isinstance(bucket, str)
        or not isinstance(bucket_region, str)
    ):
        raise ValueError("DuckLake C1 plan has an invalid stage identity")
    source_raw = raw.get("source_b1")
    if not isinstance(source_raw, dict):
        raise ValueError("DuckLake C1 plan must identify the B1 source")
    source_stage_id = source_raw.get("stage_id")
    if source_stage_id != "P2-GCE-GCS-B1-1GB-20260809-001":
        raise ValueError("DuckLake C1 plan must use the approved B1 source stage")
    source_b1 = _object(source_raw.get("object"), label="source_b1.object")
    lane_1 = _object(raw.get("a0_lane_1"), label="a0_lane_1")
    lane_2 = _object(raw.get("a0_lane_2"), label="a0_lane_2")
    if (
        lane_1.object == lane_2.object
        or lane_1.generation == lane_2.generation
        or lane_1.sha256 != lane_2.sha256
        or lane_1.physical_bytes != lane_2.physical_bytes
    ):
        raise ValueError("DuckLake C1 plan A0 lanes must be distinct byte-identical objects")
    candidate_raw = raw.get("candidate")
    if not isinstance(candidate_raw, dict):
        raise ValueError("DuckLake C1 plan candidate must be an object")
    candidate = DuckLakeC1Candidate(
        layout_id=candidate_raw.get("layout_id"),
        management_mode=candidate_raw.get("management_mode"),
        object=_object(candidate_raw.get("object"), label="candidate.object"),
    )
    if candidate.layout_id != "C1" or candidate.management_mode != _C1_MODE:
        raise ValueError("DuckLake C1 plan has an unapproved management mode")
    if candidate.object.object == source_b1.object or candidate.object.generation == source_b1.generation:
        raise ValueError("DuckLake C1 plan must use a fresh clone object")
    if (
        candidate.object.sha256 != source_b1.sha256
        or candidate.object.physical_bytes != source_b1.physical_bytes
        or candidate.object.crc32c != source_b1.crc32c
    ):
        raise ValueError("DuckLake C1 plan candidate must be byte-identical to B1")
    return DuckLakeC1Plan(
        stage_id=stage_id,
        bucket=bucket,
        bucket_region=bucket_region,
        source_b1_stage_id=source_stage_id,
        source_b1=source_b1,
        a0_lane_1=lane_1,
        a0_lane_2=lane_2,
        candidate=candidate,
    )


def configure_ducklake_c1_connection(
    connection: duckdb.DuckDBPyConnection,
    *,
    manifest: DuckLakeC1Manifest,
    httpfs_extension: Path,
    ducklake_extension: Path,
) -> None:
    """Attach C1 after explicit extension loading and ephemeral GCS OAuth setup."""

    configure_duckdb_gcs_oauth(
        connection, extension_path=httpfs_extension, bucket=manifest.bucket
    )
    extension = ducklake_extension.resolve()
    if not extension.is_file() or extension.is_symlink():
        raise FileNotFoundError(extension)
    escaped_extension = str(extension).replace("'", "''")
    escaped_catalog = str(manifest.catalog.metadata_path).replace("'", "''")
    try:
        connection.execute("SET autoinstall_known_extensions = false")
        connection.execute(f"LOAD '{escaped_extension}'")
        connection.execute("SET autoload_known_extensions = false")
        connection.execute(
            f"ATTACH 'ducklake:{escaped_catalog}' AS {manifest.catalog.catalog_alias} (READ_ONLY)"
        )
    except Exception:
        raise RuntimeError("DuckLake C1 setup failed; catalog and credential details redacted") from None


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _records(connection: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, object]]:
    result = connection.execute(sql)
    columns = [str(item[0]) for item in result.description]
    return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]


def bootstrap_ducklake_c1(
    *,
    plan_path: Path,
    output: Path,
    httpfs_extension: Path,
    ducklake_extension: Path,
) -> dict[str, object]:
    """Create a local C1 catalogue and register exactly one immutable clone object.

    This deliberately has no DuckLake writer step: C1 is a metadata-layer test over an existing,
    byte-identical Parquet clone. The final manifest is validated again before it is returned.
    """

    if output.exists() or output.is_symlink():
        raise FileExistsError(f"DuckLake C1 bootstrap output already exists: {output}")
    plan = read_ducklake_c1_plan(plan_path)
    httpfs = httpfs_extension.resolve()
    ducklake = ducklake_extension.resolve()
    if not httpfs.is_file() or httpfs.is_symlink():
        raise FileNotFoundError(httpfs)
    if not ducklake.is_file() or ducklake.is_symlink():
        raise FileNotFoundError(ducklake)
    output.mkdir(parents=True)
    metadata_path = output / "metadata.ducklake"
    unused_data_path = output / "unused-data-path"
    candidate_url = gcs_media_url_for_generation(
        plan.bucket, plan.candidate.object.object, plan.candidate.object.generation
    )
    connection = duckdb.connect()
    try:
        configure_duckdb_gcs_oauth(connection, extension_path=httpfs, bucket=plan.bucket)
        escaped_extension = str(ducklake).replace("'", "''")
        connection.execute("SET autoinstall_known_extensions = false")
        connection.execute(f"LOAD '{escaped_extension}'")
        connection.execute("SET autoload_known_extensions = false")
        connection.execute(
            f"ATTACH 'ducklake:{str(metadata_path).replace(chr(39), chr(39) * 2)}' AS c1 "
            f"(DATA_PATH {_sql_literal(str(unused_data_path))})"
        )
        connection.execute("CALL c1.set_option('data_inlining_row_limit', 0)")
        connection.execute("USE c1")
        candidate_relation = render_gcs_read_parquet(plan.bucket, (plan.candidate.object,))
        connection.execute(
            f"CREATE TABLE b1 AS SELECT * FROM {candidate_relation} LIMIT 0"
        )
        connection.execute(
            "CALL ducklake_add_data_files(" + ", ".join(
                (_sql_literal("c1"), _sql_literal("b1"), _sql_literal(candidate_url))
            ) + ")"
        )
        registered_files = _records(
            connection, "FROM ducklake_list_files('c1', 'b1', schema => 'main')"
        )
        if len(registered_files) != 1:
            raise ValueError("DuckLake C1 must register exactly one data file")
        registered = registered_files[0]
        if (
            registered.get("data_file") != candidate_url
            or registered.get("data_file_size_bytes") != plan.candidate.object.physical_bytes
        ):
            raise ValueError("DuckLake C1 registered-file metadata did not preserve the immutable object")
        settings = _records(connection, "FROM c1.settings()")
        options = _records(connection, "FROM c1.options()")
        extension_state = _records(
            connection,
            "SELECT extension_name, loaded, installed, extension_version, install_mode, installed_from "
            "FROM duckdb_extensions() WHERE extension_name IN ('ducklake', 'httpfs') ORDER BY extension_name",
        )
    finally:
        connection.close()
    if not metadata_path.is_file() or metadata_path.is_symlink():
        raise ValueError("DuckLake C1 bootstrap did not create a regular metadata file")
    unexpected_local_files = sorted(
        str(path.relative_to(output))
        for path in unused_data_path.rglob("*")
        if path.is_file() or path.is_symlink()
    ) if unused_data_path.exists() else []
    if unexpected_local_files:
        raise ValueError("DuckLake C1 bootstrap unexpectedly wrote local data files")
    manifest_path = output / "c1-stage-manifest.json"
    manifest = {
        "stage_id": plan.stage_id,
        "bucket": plan.bucket,
        "bucket_region": plan.bucket_region,
        "source_b1": {
            "stage_id": plan.source_b1_stage_id,
            "object": plan.source_b1.__dict__,
        },
        "a0_lane_1": plan.a0_lane_1.__dict__,
        "a0_lane_2": plan.a0_lane_2.__dict__,
        "candidate": {
            "layout_id": plan.candidate.layout_id,
            "management_mode": plan.candidate.management_mode,
            "object": plan.candidate.object.__dict__,
        },
        "catalog": {
            "catalog_type": "duckdb_local_single_client",
            "metadata_path": str(metadata_path.resolve()),
            "metadata_sha256": sha256(metadata_path.read_bytes()).hexdigest(),
            "catalog_alias": "c1",
            "schema": "main",
            "table": "b1",
            "read_only_during_timing": True,
        },
        "bootstrap": {
            "candidate_url": candidate_url,
            "data_path": str(unused_data_path.resolve()),
            "data_path_role": "unused_for_registered_absolute_data_file",
            "registered_files": registered_files,
            "settings": settings,
            "options": options,
            "extension_state": extension_state,
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")
    final = read_ducklake_c1_manifest(manifest_path)
    return {
        "status": "validated",
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256(manifest_path.read_bytes()).hexdigest(),
        "catalog_sha256": final.catalog.metadata_sha256,
        "registered_file_count": len(registered_files),
    }


def _sort_order_failure_count(connection: duckdb.DuckDBPyConnection, relation: str) -> int:
    return connection.execute(
        f"""
        WITH ordered AS (
            SELECT
                filter_key,
                row_id,
                lag(filter_key) OVER () AS previous_filter_key,
                lag(row_id) OVER () AS previous_row_id
            FROM {relation}
        )
        SELECT count(*)
        FROM ordered
        WHERE filter_key < previous_filter_key
           OR (filter_key = previous_filter_key AND row_id < previous_row_id)
        """
    ).fetchone()[0]


def semantic_schema(rows: list[tuple[object, ...]]) -> list[tuple[object, object, object]]:
    """Return the observable column contract, excluding display-only default metadata."""

    return [tuple(row[:3]) for row in rows]


def validate_ducklake_c1(
    *,
    manifest_path: Path,
    httpfs_extension: Path,
    ducklake_extension: Path,
    comparison: str = "a0_representation",
) -> dict[str, object]:
    """Run the untimed C1 schema, value, query-result and observed-order gate.

    ``direct_clone_increment`` is the causal management-layer gate: its direct
    relation reads the same immutable C1 object that the catalogue registers.
    The historical A0 representation remains available only to preserve the
    earlier, deliberately broader representation comparison.
    """

    if comparison not in _C1_COMPARISONS:
        raise ValueError(f"unsupported DuckLake C1 comparison: {comparison}")
    manifest = read_ducklake_c1_manifest(manifest_path)
    direct_object = (
        manifest.candidate.object if comparison == "direct_clone_increment" else manifest.source_b1
    )
    connection = duckdb.connect()
    try:
        configure_ducklake_c1_connection(
            connection,
            manifest=manifest,
            httpfs_extension=httpfs_extension,
            ducklake_extension=ducklake_extension,
        )
        direct_relation = render_gcs_read_parquet(manifest.bucket, (direct_object,))
        connection.execute(f"CREATE TEMP VIEW direct_parquet AS SELECT * FROM {direct_relation}")
        connection.execute(
            f"CREATE TEMP VIEW candidate_c1 AS SELECT * FROM {manifest.catalog.relation_sql}"
        )
        direct_schema = connection.execute("DESCRIBE direct_parquet").fetchall()
        candidate_schema = connection.execute("DESCRIBE candidate_c1").fetchall()
        try:
            value_equivalence = validate_relations(connection, "direct_parquet", "candidate_c1")
        except CorrectnessMismatch as error:
            value_equivalence = {"valid": False, "error": str(error)}
        direct_queries = run_query_suite(connection, "direct_parquet")
        candidate_queries = run_query_suite(connection, "candidate_c1")
        direct_order_failures = _sort_order_failure_count(connection, "direct_parquet")
        candidate_order_failures = _sort_order_failure_count(connection, "candidate_c1")
    finally:
        connection.close()
    direct_semantic_schema = semantic_schema(direct_schema)
    candidate_semantic_schema = semantic_schema(candidate_schema)
    valid = (
        direct_semantic_schema == candidate_semantic_schema
        and value_equivalence.get("valid") is True
        and direct_queries == candidate_queries
        and direct_order_failures == 0
        and candidate_order_failures == 0
    )
    return {
        "valid": valid,
        "comparison": comparison,
        "direct_object": {
            "object": direct_object.object,
            "generation": direct_object.generation,
            "sha256": direct_object.sha256,
            "physical_bytes": direct_object.physical_bytes,
            "crc32c": direct_object.crc32c,
        },
        "schema_match": direct_semantic_schema == candidate_semantic_schema,
        "schema": {
            "comparison": "name_type_nullability",
            "direct": direct_schema,
            "candidate": candidate_schema,
            "direct_semantic": direct_semantic_schema,
            "candidate_semantic": candidate_semantic_schema,
        },
        "value_equivalence": value_equivalence,
        "query_results_match": direct_queries == candidate_queries,
        "query_results": {"direct_parquet": direct_queries, "candidate_c1": candidate_queries},
        "physical_order": {
            "order": "filter_key,row_id",
            "direct_parquet_failure_rows": direct_order_failures,
            "candidate_c1_failure_rows": candidate_order_failures,
        },
    }
