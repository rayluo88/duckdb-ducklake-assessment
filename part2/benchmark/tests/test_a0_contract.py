from __future__ import annotations

import hashlib

from ducklake_benchmark.a0_contract import (
    REQUIRED_WRITER_CONTROLS,
    HistoricalControl,
    build_a0_contract,
    reproduce_a0_reference,
    write_a0_contract,
)
from ducklake_benchmark.fixture import build_local_fixture
from ducklake_benchmark.model import Scenario, SyntheticProfile


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _historically_proven_controls() -> dict[str, HistoricalControl]:
    return {
        control: HistoricalControl(value=f"approved-{control}", provenance="historically_proven")
        for control in REQUIRED_WRITER_CONTROLS
    }


def test_contract_inventories_a0_without_claiming_unproven_writer_history(tmp_path) -> None:
    artifacts = build_local_fixture(
        tmp_path / "fixture",
        SyntheticProfile(rows=1_000, seed=17, scenario=Scenario.SHUFFLED),
    )

    contract = build_a0_contract(
        database_path=artifacts.database_path,
        a0_path=artifacts.parquet_path,
        historical_controls={},
    )

    assert contract["valid"] is False
    assert contract["requires_design_amendment"] is True
    assert contract["source"]["database"] == {
        "bytes": artifacts.database_path.stat().st_size,
        "sha256": _sha256(artifacts.database_path),
    }
    assert contract["source"]["a0"] == {
        "bytes": artifacts.parquet_path.stat().st_size,
        "sha256": _sha256(artifacts.parquet_path),
    }
    assert contract["parquet"]["file_count"] == 1
    assert contract["parquet"]["row_count"] == 1_000
    assert contract["schema"][0] == {"name": "row_id", "type": "BIGINT", "nullable": True}
    assert all(
        value["provenance"] == "observed"
        for value in contract["controls"].values()
    )
    assert contract["environment"]["duckdb_settings"]
    assert contract["environment"]["duckdb_extensions"]


def test_contract_fails_closed_when_any_control_is_inferred(tmp_path) -> None:
    artifacts = build_local_fixture(
        tmp_path / "fixture",
        SyntheticProfile(rows=100, seed=18, scenario=Scenario.SHUFFLED),
    )
    controls = _historically_proven_controls()
    controls["parquet_version"] = HistoricalControl(
        value="v2", provenance="reproduced_inferred"
    )

    contract = build_a0_contract(
        database_path=artifacts.database_path,
        a0_path=artifacts.parquet_path,
        historical_controls=controls,
    )

    assert contract["valid"] is False
    assert contract["requires_design_amendment"] is True
    assert contract["controls"]["parquet_version"]["provenance"] == "reproduced_inferred"
    assert "parquet_version" in contract["amendment_required_for"]


def test_contract_writes_once_only_when_every_control_is_historically_proven(tmp_path) -> None:
    artifacts = build_local_fixture(
        tmp_path / "fixture",
        SyntheticProfile(rows=100, seed=19, scenario=Scenario.SHUFFLED),
    )
    output_path = tmp_path / "a0-contract.json"

    contract = write_a0_contract(
        database_path=artifacts.database_path,
        a0_path=artifacts.parquet_path,
        historical_controls=_historically_proven_controls(),
        output_path=output_path,
    )

    assert contract["valid"] is True
    assert output_path.is_file()

    try:
        write_a0_contract(
            database_path=artifacts.database_path,
            a0_path=artifacts.parquet_path,
            historical_controls=_historically_proven_controls(),
            output_path=output_path,
        )
    except FileExistsError as error:
        assert "A0 contract output already exists" in str(error)
    else:
        raise AssertionError("existing A0 contract output was overwritten")


def test_reproduction_checks_logical_and_causal_physical_attributes(tmp_path) -> None:
    artifacts = build_local_fixture(
        tmp_path / "fixture",
        SyntheticProfile(rows=1_000, seed=20, scenario=Scenario.SHUFFLED),
    )
    controls = _historically_proven_controls()
    controls.update(
        {
            "writer_threads": HistoricalControl(1, "historically_proven"),
            "compression": HistoricalControl("snappy", "historically_proven"),
            "row_group_size": HistoricalControl(122_880, "historically_proven"),
            "parquet_version": HistoricalControl(1, "historically_proven"),
            "string_dictionary_page_size_limit": HistoricalControl(1_048_576, "historically_proven"),
            "preserve_insertion_order": HistoricalControl(True, "historically_proven"),
            "per_thread_output": HistoricalControl(False, "historically_proven"),
            "write_partition_columns": HistoricalControl(False, "historically_proven"),
            "append": HistoricalControl(False, "historically_proven"),
            "overwrite": HistoricalControl(False, "historically_proven"),
        }
    )

    comparison = reproduce_a0_reference(
        database_path=artifacts.database_path,
        source_table="synthetic_data",
        a0_path=artifacts.parquet_path,
        output_path=tmp_path / "a0-reference.parquet",
        historical_controls=controls,
    )

    assert comparison["valid"] is True
    assert comparison["logical_equivalence"]["valid"] is True
    assert comparison["causal_attributes"]["file_count_match"] is True
    assert comparison["causal_attributes"]["format_version_match"] is True
    assert comparison["causal_attributes"]["codecs_match"] is True
    assert comparison["causal_attributes"]["row_group_rows_match"] is True
