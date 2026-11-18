from __future__ import annotations

import pytest

from ducklake_benchmark.layouts import layout_specs
from ducklake_benchmark.layouts import evaluate_b2_campaign_size_gate
from ducklake_benchmark.layouts import generate_layout
from ducklake_benchmark.layouts import render_writer_sql
from ducklake_benchmark.fixture import build_local_fixture
from ducklake_benchmark.model import Scenario
from ducklake_benchmark.model import SyntheticProfile


def test_layout_specs_are_the_frozen_three_factor_matrix() -> None:
    specs = layout_specs()

    assert [spec.layout_id for spec in specs] == ["B1", "B2", "B3", "Bstar"]
    assert [spec.provenance for spec in specs] == ["assumed", "assumed", "assumed", "approved_combination_20260809"]
    assert all(spec.writer_threads == 1 for spec in specs)
    assert specs[0].order_by == ("filter_key", "row_id")
    assert specs[1].file_size_bytes == "128MB"
    assert specs[2].compression == "zstd"
    assert specs[2].compression_level == 3
    assert specs[0].compression == specs[1].compression == "snappy"
    assert specs[0].row_group_size == specs[1].row_group_size == specs[2].row_group_size == 122_880
    assert all(spec.parquet_version == 1 for spec in specs)
    assert all(spec.dictionary_page_size_limit == 1_048_576 for spec in specs)
    assert specs[1].per_thread_output is True
    assert specs[1].filename_pattern == "part_{i}"
    assert all(spec.append is False and spec.overwrite is False for spec in specs)


def test_b1_writer_sql_sorts_by_filter_key_and_row_id(tmp_path) -> None:
    sql = render_writer_sql(
        layout_specs()[0], source_table="synthetic_data", output_path=tmp_path / "b1.parquet"
    )

    assert "SELECT * FROM synthetic_data ORDER BY filter_key, row_id" in sql
    assert "COMPRESSION snappy" in sql
    assert "PER_THREAD_OUTPUT false" in sql
    assert "FILE_SIZE_BYTES" not in sql


def test_b2_writer_sql_uses_fresh_directory_and_128mb_target(tmp_path) -> None:
    sql = render_writer_sql(
        layout_specs()[1], source_table="synthetic_data", output_path=tmp_path / "b2"
    )

    assert "SELECT * FROM synthetic_data\n" in sql
    assert "ORDER BY" not in sql
    assert "FILE_SIZE_BYTES '128MB'" in sql
    assert "PER_THREAD_OUTPUT true" in sql
    assert "FILENAME_PATTERN 'part_{i}'" in sql


def test_b3_writer_sql_uses_zstd_level_three(tmp_path) -> None:
    sql = render_writer_sql(
        layout_specs()[2], source_table="synthetic_data", output_path=tmp_path / "b3.parquet"
    )

    assert "COMPRESSION zstd" in sql
    assert "COMPRESSION_LEVEL 3" in sql
    assert "PER_THREAD_OUTPUT false" in sql


def test_writer_sql_escapes_output_paths_and_refuses_existing_output(tmp_path) -> None:
    apostrophe_path = tmp_path / "B1's-layout.parquet"
    sql = render_writer_sql(
        layout_specs()[0], source_table="synthetic_data", output_path=apostrophe_path
    )
    assert "B1''s-layout.parquet" in sql

    apostrophe_path.touch()
    with pytest.raises(FileExistsError, match="layout output already exists"):
        render_writer_sql(
            layout_specs()[0], source_table="synthetic_data", output_path=apostrophe_path
        )


def test_writer_sql_rejects_unsafe_source_table_identifier(tmp_path) -> None:
    with pytest.raises(ValueError, match="source_table must be a safe SQL identifier"):
        render_writer_sql(
            layout_specs()[0], source_table="synthetic_data; DROP TABLE x", output_path=tmp_path / "x"
        )


@pytest.mark.parametrize("spec", layout_specs(), ids=lambda spec: spec.layout_id)
def test_generate_layout_writes_complete_small_fixture_manifest(tmp_path, spec) -> None:
    source = build_local_fixture(
        tmp_path / "fixture",
        SyntheticProfile(rows=2_000, seed=21, scenario=Scenario.SHUFFLED),
    )

    manifest = generate_layout(
        spec,
        database_path=source.database_path,
        a0_path=source.parquet_path,
        source_table="synthetic_data",
        output_dir=tmp_path / f"{spec.layout_id.lower()}-layout",
    )

    assert manifest["layout_id"] == spec.layout_id
    assert manifest["provenance"] == spec.provenance
    assert manifest["source"]["database"]["bytes"] == source.database_path.stat().st_size
    assert manifest["source"]["a0"]["bytes"] == source.parquet_path.stat().st_size
    assert manifest["writer"]["sql"].startswith("COPY")
    assert manifest["writer"]["settings"]["threads"] == "1"
    assert manifest["environment"]["duckdb_version"]
    assert manifest["generation"]["duration_ns"] > 0
    assert manifest["files"]
    assert all(file["bytes"] > 0 and len(file["sha256"]) == 64 for file in manifest["files"])
    assert all(file["row_groups"] and file["codecs"] for file in manifest["files"])
    if spec.layout_id == "B2":
        assert manifest["campaign_size_gate"] == {"evaluated": False, "status": "not_evaluated"}


def test_generate_layout_refuses_existing_output_directory(tmp_path) -> None:
    source = build_local_fixture(
        tmp_path / "fixture",
        SyntheticProfile(rows=100, seed=22, scenario=Scenario.SHUFFLED),
    )
    output_dir = tmp_path / "existing-layout"
    output_dir.mkdir()

    with pytest.raises(FileExistsError, match="layout output directory already exists"):
        generate_layout(
            layout_specs()[0],
            database_path=source.database_path,
            a0_path=source.parquet_path,
            source_table="synthetic_data",
            output_dir=output_dir,
        )


def test_b2_campaign_size_gate_accepts_only_the_predeclared_envelope() -> None:
    mib = 1_048_576
    accepted = evaluate_b2_campaign_size_gate(
        [
            {"bytes": 112 * mib, "writer_index": 0},
            {"bytes": 128 * mib, "writer_index": 1},
            {"bytes": 128 * mib, "writer_index": 2},
            {"bytes": 128 * mib, "writer_index": 3},
            {"bytes": 128 * mib, "writer_index": 4},
            {"bytes": 128 * mib, "writer_index": 5},
            {"bytes": 128 * mib, "writer_index": 6},
            {"bytes": 64 * mib, "writer_index": 7},
        ]
    )
    assert accepted == {"evaluated": True, "status": "accepted"}

    rejected = evaluate_b2_campaign_size_gate(
        [
            {"bytes": 240 * mib, "writer_index": index}
            for index in range(4)
        ]
    )
    assert rejected["evaluated"] is True
    assert rejected["status"] == "unsupported"
    assert "file_count" in rejected["reasons"]
    assert "non_final_size" in rejected["reasons"]
