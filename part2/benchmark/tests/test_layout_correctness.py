from __future__ import annotations

import copy

import pytest

from ducklake_benchmark.fixture import build_local_fixture
from ducklake_benchmark.layout_correctness import render_read_parquet
from ducklake_benchmark.layout_correctness import validate_layout
from ducklake_benchmark.layouts import generate_layout
from ducklake_benchmark.layouts import layout_specs
from ducklake_benchmark.model import Scenario
from ducklake_benchmark.model import SyntheticProfile


@pytest.mark.parametrize("spec", layout_specs(), ids=lambda spec: spec.layout_id)
def test_layout_validation_checks_values_queries_order_and_declared_physical_factor(tmp_path, spec) -> None:
    fixture = build_local_fixture(
        tmp_path / "fixture",
        SyntheticProfile(rows=2_000, seed=23, scenario=Scenario.SHUFFLED),
    )
    output_dir = tmp_path / spec.layout_id
    manifest = generate_layout(
        spec,
        database_path=fixture.database_path,
        a0_path=fixture.parquet_path,
        source_table="synthetic_data",
        output_dir=output_dir,
    )

    result = validate_layout(
        manifest=manifest,
        representation_root=output_dir,
        database_path=fixture.database_path,
        source_table="synthetic_data",
    )

    assert result["status"] == "validated"
    assert result["valid"] is True
    assert result["correctness"]["value_equivalence"]["valid"] is True
    assert result["correctness"]["query_results_match"] is True
    assert result["causal_isolation"]["valid"] is True
    assert result["resolved_files"]


def test_layout_validation_rejects_changed_source_identity_before_querying(tmp_path) -> None:
    fixture = build_local_fixture(
        tmp_path / "fixture",
        SyntheticProfile(rows=100, seed=24, scenario=Scenario.SHUFFLED),
    )
    output_dir = tmp_path / "B1"
    manifest = generate_layout(
        layout_specs()[0],
        database_path=fixture.database_path,
        a0_path=fixture.parquet_path,
        source_table="synthetic_data",
        output_dir=output_dir,
    )
    corrupted = copy.deepcopy(manifest)
    corrupted["source"]["database"]["sha256"] = "0" * 64

    result = validate_layout(
        manifest=corrupted,
        representation_root=output_dir,
        database_path=fixture.database_path,
        source_table="synthetic_data",
    )

    assert result == {
        "valid": False,
        "status": "invalid_correctness",
        "failures": ["source_database_identity"],
    }


def test_read_parquet_sql_quotes_paths_and_rejects_manifest_path_escape(tmp_path) -> None:
    safe_file = tmp_path / "quoted'name.parquet"
    safe_file.touch()
    assert "quoted''name.parquet" in render_read_parquet([safe_file])

    with pytest.raises(ValueError, match="must remain below representation root"):
        validate_layout(
            manifest={
                "layout_id": "B1",
                "source": {},
                "files": [{"path": "../outside.parquet", "bytes": 1, "sha256": "0" * 64}],
            },
            representation_root=tmp_path,
            database_path=tmp_path / "missing.duckdb",
            source_table="synthetic_data",
        )
