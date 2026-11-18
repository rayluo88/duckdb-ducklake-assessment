import re

import duckdb


class CorrectnessMismatch(AssertionError):
    """Raised when two benchmark representations are not equivalent."""


def validate_relations(
    connection: duckdb.DuckDBPyConnection,
    left_relation: str,
    right_relation: str,
    *,
    absolute_tolerance: float = 1e-9,
    relative_tolerance: float = 1e-9,
) -> dict[str, object]:
    identifier_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    for relation in (left_relation, right_relation):
        if not identifier_pattern.fullmatch(relation):
            raise ValueError(f"relation must be a safe SQL identifier: {relation!r}")

    exact_columns = (
        "row_id, subject_id, event_date, cohort, filter_key, nonaligned_key, measure, "
        "text_value, nullable_value"
    )
    left_count, right_count = connection.execute(
        f"""
        SELECT
            (SELECT count(*) FROM {left_relation}),
            (SELECT count(*) FROM {right_relation})
        """
    ).fetchone()
    exact_difference_rows = connection.execute(
        f"""
        SELECT count(*)
        FROM (
            (SELECT {exact_columns} FROM {left_relation}
             EXCEPT ALL
             SELECT {exact_columns} FROM {right_relation})
            UNION ALL
            (SELECT {exact_columns} FROM {right_relation}
             EXCEPT ALL
             SELECT {exact_columns} FROM {left_relation})
        ) AS differences
        """
    ).fetchone()[0]
    if left_count != right_count or exact_difference_rows:
        raise CorrectnessMismatch(
            "exact multiset mismatch: "
            f"left_rows={left_count}, right_rows={right_count}, "
            f"difference_rows={exact_difference_rows}"
        )

    left_fingerprint = connection.execute(
        f"SELECT bit_xor(hash({exact_columns})) FROM {left_relation}"
    ).fetchone()[0]
    right_fingerprint = connection.execute(
        f"SELECT bit_xor(hash({exact_columns})) FROM {right_relation}"
    ).fetchone()[0]
    max_difference, max_scale = connection.execute(
        f"""
        SELECT
            coalesce(max(abs(left_side.floating_measure - right_side.floating_measure)), 0.0),
            greatest(
                coalesce(max(abs(left_side.floating_measure)), 0.0),
                coalesce(max(abs(right_side.floating_measure)), 0.0)
            )
        FROM {left_relation} AS left_side
        JOIN {right_relation} AS right_side USING (row_id)
        """
    ).fetchone()
    tolerance = absolute_tolerance + relative_tolerance * max_scale
    if max_difference > tolerance:
        raise CorrectnessMismatch(
            "floating tolerance mismatch: "
            f"max_difference={max_difference}, allowed={tolerance}"
        )

    left_measure_sum = connection.execute(
        f"SELECT sum(measure) FROM {left_relation}"
    ).fetchone()[0]
    right_measure_sum = connection.execute(
        f"SELECT sum(measure) FROM {right_relation}"
    ).fetchone()[0]
    return {
        "valid": True,
        "row_count": left_count,
        "exact_multiset_difference_rows": exact_difference_rows,
        "exact_measure_sum": str(left_measure_sum),
        "exact_measure_sum_match": left_measure_sum == right_measure_sum,
        "fingerprint": left_fingerprint,
        "fingerprint_match": left_fingerprint == right_fingerprint,
        "fingerprint_evidence": "probabilistic_support_only",
        "floating_max_absolute_difference": max_difference,
        "floating_allowed_difference": tolerance,
        "absolute_tolerance": absolute_tolerance,
        "relative_tolerance": relative_tolerance,
    }
