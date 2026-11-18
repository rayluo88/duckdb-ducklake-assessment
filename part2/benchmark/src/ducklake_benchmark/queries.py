from dataclasses import dataclass
from decimal import Decimal

import duckdb


@dataclass(frozen=True)
class QuerySpec:
    query_id: str
    category: str
    primary: bool
    sql_template: str


def query_specs() -> tuple[QuerySpec, ...]:
    return (
        QuerySpec(
            "Q0",
            "metadata_diagnostic",
            False,
            "SELECT count(*)::BIGINT AS row_count FROM {relation}",
        ),
        QuerySpec(
            "Q1",
            "narrow_full_scan",
            True,
            "SELECT sum(measure)::DECIMAL(38, 2) AS measure_sum FROM {relation}",
        ),
        QuerySpec(
            "Q2a",
            "aligned_selective_0_1_percent",
            True,
            "SELECT count(*)::BIGINT AS row_count, sum(measure)::DECIMAL(38, 2) AS measure_sum "
            "FROM {relation} WHERE filter_key < 1",
        ),
        QuerySpec(
            "Q2b",
            "aligned_selective_1_percent",
            True,
            "SELECT count(*)::BIGINT AS row_count, sum(measure)::DECIMAL(38, 2) AS measure_sum "
            "FROM {relation} WHERE filter_key < 10",
        ),
        QuerySpec(
            "Q2c",
            "aligned_selective_10_percent",
            True,
            "SELECT count(*)::BIGINT AS row_count, sum(measure)::DECIMAL(38, 2) AS measure_sum "
            "FROM {relation} WHERE filter_key < 100",
        ),
        QuerySpec(
            "Q3",
            "nonaligned_guard",
            True,
            "SELECT count(*)::BIGINT AS row_count, sum(measure)::DECIMAL(38, 2) AS measure_sum "
            "FROM {relation} WHERE nonaligned_key < 10",
        ),
        QuerySpec(
            "Q4",
            "multi_column_filter",
            True,
            "SELECT count(*)::BIGINT AS row_count, "
            "bit_xor(hash(row_id, subject_id, measure)) AS fingerprint "
            "FROM {relation} WHERE cohort = 'C1' AND filter_key < 100",
        ),
        QuerySpec(
            "Q5",
            "group_by",
            True,
            "SELECT cohort, count(*)::BIGINT AS row_count, "
            "sum(measure)::DECIMAL(38, 2) AS measure_sum "
            "FROM {relation} GROUP BY cohort ORDER BY cohort",
        ),
        QuerySpec(
            "Q6",
            "small_mapping_join",
            True,
            "WITH mapping(cohort, label) AS (VALUES "
            "('C0', 'group_0'), ('C1', 'group_1'), ('C2', 'group_2'), ('C3', 'group_3')) "
            "SELECT mapping.label, count(*)::BIGINT AS row_count, "
            "sum(source.measure)::DECIMAL(38, 2) AS measure_sum "
            "FROM {relation} AS source JOIN mapping USING (cohort) "
            "GROUP BY mapping.label ORDER BY mapping.label",
        ),
    )


def run_query_suite(
    connection: duckdb.DuckDBPyConnection, relation: str
) -> dict[str, dict[str, object]]:
    results: dict[str, dict[str, object]] = {}
    for spec in query_specs():
        cursor = connection.execute(spec.sql_template.format(relation=relation))
        columns = [column[0] for column in cursor.description]
        rows = [
            [str(value) if isinstance(value, Decimal) else value for value in row]
            for row in cursor.fetchall()
        ]
        results[spec.query_id] = {
            "category": spec.category,
            "primary": spec.primary,
            "columns": columns,
            "rows": rows,
        }
    return results
