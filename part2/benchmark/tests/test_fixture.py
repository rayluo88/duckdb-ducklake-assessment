import json

import duckdb
import pytest

from ducklake_benchmark.fixture import build_local_fixture
from ducklake_benchmark.model import Scenario, SyntheticProfile


def test_build_local_fixture_creates_equivalent_a0_and_d(tmp_path) -> None:
    artifacts = build_local_fixture(
        tmp_path,
        SyntheticProfile(rows=1_000, seed=11, scenario=Scenario.SHUFFLED),
    )

    assert artifacts.database_path.is_file()
    assert artifacts.parquet_path.is_file()
    assert artifacts.manifest_path.is_file()

    with duckdb.connect(str(artifacts.database_path), read_only=True) as connection:
        native_count = connection.execute("SELECT count(*) FROM synthetic_data").fetchone()[0]
        parquet_count = connection.execute(
            "SELECT count(*) FROM read_parquet(?)", [str(artifacts.parquet_path)]
        ).fetchone()[0]

    assert native_count == parquet_count == 1_000

    manifest = json.loads(artifacts.manifest_path.read_text())
    assert manifest["manifest_version"] == 1
    assert manifest["profile"]["rows"] == {"value": 1_000, "provenance": "assumed"}
    assert manifest["representations"]["A0"]["kind"] == "duckdb_default_write_parquet"
    assert manifest["representations"]["D"]["kind"] == "native_duckdb_table"
    assert manifest["parquet"]["file_count"] == 1
    assert manifest["parquet"]["row_count"] == 1_000


def test_synthetic_scenarios_change_aligned_key_clustering(tmp_path) -> None:
    shuffled = build_local_fixture(
        tmp_path / "shuffled",
        SyntheticProfile(rows=2_000, seed=5, scenario=Scenario.SHUFFLED),
    )
    clustered = build_local_fixture(
        tmp_path / "clustered",
        SyntheticProfile(rows=2_000, seed=5, scenario=Scenario.STRONGLY_CLUSTERED),
    )

    def adjacent_matches(database_path) -> int:
        with duckdb.connect(str(database_path), read_only=True) as connection:
            return connection.execute(
                """
                SELECT count(*) FILTER (WHERE filter_key = previous_key)
                FROM (
                    SELECT filter_key, lag(filter_key) OVER (ORDER BY row_id) AS previous_key
                    FROM synthetic_data
                )
                """
            ).fetchone()[0]

    assert adjacent_matches(clustered.database_path) > adjacent_matches(shuffled.database_path)


def test_build_local_fixture_refuses_to_overwrite_evidence(tmp_path) -> None:
    profile = SyntheticProfile(rows=100)
    build_local_fixture(tmp_path, profile)

    with pytest.raises(FileExistsError, match="fixture output already exists"):
        build_local_fixture(tmp_path, profile)
