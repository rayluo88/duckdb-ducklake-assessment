from __future__ import annotations

import csv
from pathlib import Path

from experiment import build_synthetic_csv, run_comparison


def test_build_synthetic_csv_is_deterministic(tmp_path: Path) -> None:
    csv_path = tmp_path / "measurements.csv"

    row_count = build_synthetic_csv(csv_path, row_count=30)

    assert row_count == 30
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 30
    assert rows[0] == {
        "participant_id": "S00001",
        "cohort": "HELIOS",
        "visit_date": "2026-01-02",
        "age_years": "21",
        "biomarker_value": "50.25",
        "review_flag": "false",
    }
    assert rows[-1]["participant_id"] == "S00030"


def test_comparison_preserves_baseline_results_and_reopens(tmp_path: Path) -> None:
    results = run_comparison(tmp_path / "generated", row_count=120)

    assert results["duckdb_version"]
    assert results["ducklake_extension_version"]
    assert results["row_count"] == 120
    assert results["baseline_results_equal"] is True
    assert results["raw_csv"]["summary"] == results["plain_parquet"]["summary"]
    assert results["plain_parquet"]["summary"] == results["ducklake"]["initial_summary"]
    assert results["ducklake"]["reopened_row_count"] == 120
    assert results["ducklake"]["snapshot_count_after_update"] > (
        results["ducklake"]["snapshot_count_before_update"]
    )
    assert results["ducklake"]["updated_value"] == 99.99
    assert results["ducklake"]["parquet_file_count"] >= 1
    assert results["ducklake"]["catalog_file_exists"] is True
