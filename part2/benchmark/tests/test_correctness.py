import duckdb
import pytest

from ducklake_benchmark.correctness import CorrectnessMismatch, validate_relations
from ducklake_benchmark.fixture import build_local_fixture
from ducklake_benchmark.model import SyntheticProfile


def _connection_with_parquet_view(tmp_path):
    artifacts = build_local_fixture(tmp_path, SyntheticProfile(rows=1_000, seed=13))
    connection = duckdb.connect(str(artifacts.database_path))
    parquet_path = str(artifacts.parquet_path).replace("'", "''")
    connection.execute(
        f"CREATE VIEW parquet_data AS SELECT * FROM read_parquet('{parquet_path}')"
    )
    return connection


def test_correctness_gate_accepts_equivalent_native_and_parquet(tmp_path) -> None:
    with _connection_with_parquet_view(tmp_path) as connection:
        result = validate_relations(connection, "synthetic_data", "parquet_data")

    assert result["valid"] is True
    assert result["row_count"] == 1_000
    assert result["exact_multiset_difference_rows"] == 0
    assert result["fingerprint_match"] is True
    assert result["floating_max_absolute_difference"] == 0.0


def test_correctness_gate_rejects_exact_value_change(tmp_path) -> None:
    with _connection_with_parquet_view(tmp_path) as connection:
        connection.execute(
            """
            CREATE VIEW changed_exact AS
            SELECT * REPLACE (measure + 1.00 AS measure)
            FROM parquet_data
            """
        )

        with pytest.raises(CorrectnessMismatch, match="exact multiset"):
            validate_relations(connection, "synthetic_data", "changed_exact")


def test_correctness_gate_uses_predeclared_float_tolerance(tmp_path) -> None:
    with _connection_with_parquet_view(tmp_path) as connection:
        connection.execute(
            """
            CREATE VIEW near_float AS
            SELECT * REPLACE (floating_measure + 1e-12 AS floating_measure)
            FROM parquet_data
            """
        )
        result = validate_relations(
            connection,
            "synthetic_data",
            "near_float",
            absolute_tolerance=1e-9,
            relative_tolerance=1e-9,
        )
        assert result["valid"] is True

        connection.execute(
            """
            CREATE VIEW far_float AS
            SELECT * REPLACE (floating_measure + 0.1 AS floating_measure)
            FROM parquet_data
            """
        )
        with pytest.raises(CorrectnessMismatch, match="floating tolerance"):
            validate_relations(
                connection,
                "synthetic_data",
                "far_float",
                absolute_tolerance=1e-9,
                relative_tolerance=1e-9,
            )


def test_correctness_gate_rejects_unsafe_relation_names(tmp_path) -> None:
    with _connection_with_parquet_view(tmp_path) as connection:
        with pytest.raises(ValueError, match="safe SQL identifier"):
            validate_relations(connection, "synthetic_data; DROP TABLE synthetic_data", "parquet_data")
