import duckdb

from ducklake_benchmark.fixture import build_local_fixture
from ducklake_benchmark.model import SyntheticProfile
from ducklake_benchmark.queries import query_specs, run_query_suite


def test_query_suite_contains_metadata_and_nonaligned_guard_roles() -> None:
    specs = query_specs()

    assert [spec.query_id for spec in specs] == [
        "Q0",
        "Q1",
        "Q2a",
        "Q2b",
        "Q2c",
        "Q3",
        "Q4",
        "Q5",
        "Q6",
    ]
    assert specs[0].category == "metadata_diagnostic"
    assert specs[0].primary is False
    q3 = next(spec for spec in specs if spec.query_id == "Q3")
    assert q3.category == "nonaligned_guard"
    assert q3.primary is True
    assert "nonaligned_key" in q3.sql_template


def test_query_suite_returns_identical_small_results_for_a0_and_d(tmp_path) -> None:
    artifacts = build_local_fixture(tmp_path, SyntheticProfile(rows=10_000, seed=17))
    parquet_path = str(artifacts.parquet_path).replace("'", "''")

    with duckdb.connect(str(artifacts.database_path)) as connection:
        connection.execute(
            f"CREATE VIEW parquet_data AS SELECT * FROM read_parquet('{parquet_path}')"
        )
        native_results = run_query_suite(connection, "synthetic_data")
        parquet_results = run_query_suite(connection, "parquet_data")

    assert native_results == parquet_results
    assert native_results["Q0"]["rows"] == [[10_000]]
    assert native_results["Q2a"]["rows"][0][0] < native_results["Q2c"]["rows"][0][0]
    assert all(len(result["rows"]) <= 4 for result in native_results.values())
