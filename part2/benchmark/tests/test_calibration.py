import math

import pytest

from ducklake_benchmark.calibration import (
    CalibrationConfig,
    PairedObservation,
    RemoteCalibrationLanes,
    calibrate_parquet_uris,
    calibrate_parquet_pair,
    prepare_identical_lanes,
    summarise_observations,
)
from ducklake_benchmark.fixture import build_local_fixture
from ducklake_benchmark.model import SyntheticProfile
from ducklake_benchmark.queries import query_specs


def test_calibration_config_locks_the_predeclared_default_rule() -> None:
    config = CalibrationConfig()

    assert config.warmups == 2
    assert config.initial_pairs == 7
    assert config.pair_increment == 4
    assert config.max_pairs == 19
    assert config.bootstrap_samples == 2_000
    assert config.precision_ratio == pytest.approx(1.05)


@pytest.mark.parametrize(
    "overrides",
    [
        {"warmups": -1},
        {"initial_pairs": 0},
        {"pair_increment": 0},
        {"max_pairs": 6},
        {"bootstrap_samples": 0},
        {"precision_ratio": 1.0},
    ],
)
def test_calibration_config_rejects_invalid_rules(overrides) -> None:
    with pytest.raises(ValueError):
        CalibrationConfig(**overrides)


def test_summary_reports_raw_pairs_distribution_ci_and_noise_bound() -> None:
    observations = [
        PairedObservation(block=1, first_lane="lane_1", lane_1_ns=100, lane_2_ns=100),
        PairedObservation(block=2, first_lane="lane_2", lane_1_ns=200, lane_2_ns=100),
        PairedObservation(block=3, first_lane="lane_1", lane_1_ns=300, lane_2_ns=100),
    ]

    summary = summarise_observations(observations, bootstrap_samples=200, seed=17)

    assert summary["pair_count"] == 3
    assert summary["lane_1"]["median_ns"] == 200
    assert summary["lane_1"]["iqr_ns"] == 100
    assert summary["lane_2"]["median_ns"] == 100
    assert summary["paired_log_gains"] == pytest.approx([0.0, math.log(2), math.log(3)])
    assert summary["median_paired_log_gain"] == pytest.approx(math.log(2))
    assert summary["noise_bound_log"] == pytest.approx(
        math.log(2) + 0.9 * (math.log(3) - math.log(2))
    )
    assert summary["noise_bound_ratio"] == pytest.approx(
        math.exp(summary["noise_bound_log"])
    )
    assert summary["bootstrap_95_ci_log"]["lower"] <= math.log(2)
    assert summary["bootstrap_95_ci_log"]["upper"] >= math.log(2)
    assert summary == summarise_observations(observations, bootstrap_samples=200, seed=17)


def test_summary_rejects_nonpositive_durations() -> None:
    observations = [
        PairedObservation(block=1, first_lane="lane_1", lane_1_ns=0, lane_2_ns=100)
    ]

    with pytest.raises(ValueError, match="positive"):
        summarise_observations(observations, bootstrap_samples=100, seed=1)


def test_local_calibration_uses_identical_files_and_real_duckdb_queries(tmp_path) -> None:
    fixture = build_local_fixture(tmp_path / "fixture", SyntheticProfile(rows=2_000, seed=31))
    lanes = prepare_identical_lanes(fixture.parquet_path, tmp_path / "lanes")
    config = CalibrationConfig(
        warmups=1,
        initial_pairs=3,
        pair_increment=2,
        max_pairs=3,
        bootstrap_samples=100,
    )

    results = calibrate_parquet_pair(
        lanes,
        config=config,
        seed=31,
        specs=(next(spec for spec in query_specs() if spec.query_id == "Q1"),),
    )

    assert lanes.lane_1_path.read_bytes() == lanes.lane_2_path.read_bytes()
    assert lanes.lane_1_sha256 == lanes.lane_2_sha256
    assert results["lane_integrity"]["byte_identical"] is True
    assert results["config"]["warmups"] == 1
    q1 = results["queries"]["Q1"]
    assert q1["first_query"]["result_match"] is True
    assert q1["warmup_count_per_lane"] == 1
    assert q1["pair_count"] == 3
    assert len(q1["observations"]) == 3
    assert all(item["lane_1_ns"] > 0 and item["lane_2_ns"] > 0 for item in q1["observations"])
    assert q1["precision_sufficient"] in {True, False}
    assert q1["status"] in {"stable", "unstable_at_pair_cap"}


def test_preparing_lanes_refuses_to_overwrite_evidence(tmp_path) -> None:
    fixture = build_local_fixture(tmp_path / "fixture", SyntheticProfile(rows=100))
    output = tmp_path / "lanes"
    prepare_identical_lanes(fixture.parquet_path, output)

    with pytest.raises(FileExistsError):
        prepare_identical_lanes(fixture.parquet_path, output)


def test_remote_calibration_uses_preverified_identical_sources(tmp_path) -> None:
    fixture = build_local_fixture(tmp_path / "fixture", SyntheticProfile(rows=2_000, seed=47))
    physical_bytes = fixture.parquet_path.stat().st_size
    lanes = RemoteCalibrationLanes(
        lane_1_uri=str(fixture.parquet_path),
        lane_2_uri=str(fixture.parquet_path),
        lane_1_sha256="a" * 64,
        lane_2_sha256="a" * 64,
        physical_bytes_per_lane=physical_bytes,
    )

    results = calibrate_parquet_uris(
        lanes,
        config=CalibrationConfig(
            warmups=0,
            initial_pairs=1,
            pair_increment=1,
            max_pairs=1,
            bootstrap_samples=10,
        ),
        seed=47,
        specs=(next(spec for spec in query_specs() if spec.query_id == "Q1"),),
    )

    assert results["lane_integrity"] == {
        "byte_identical": True,
        "lane_1_sha256": "a" * 64,
        "lane_2_sha256": "a" * 64,
        "physical_bytes_per_lane": physical_bytes,
    }
    assert results["queries"]["Q1"]["first_query"]["result_match"] is True


def test_remote_calibration_rejects_unmatched_provenance() -> None:
    lanes = RemoteCalibrationLanes(
        lane_1_uri="https://example.test/one.parquet",
        lane_2_uri="https://example.test/two.parquet",
        lane_1_sha256="a" * 64,
        lane_2_sha256="b" * 64,
        physical_bytes_per_lane=100,
    )

    with pytest.raises(ValueError, match="byte-identical"):
        calibrate_parquet_uris(
            lanes,
            config=CalibrationConfig(initial_pairs=1, max_pairs=1),
            seed=1,
            specs=(next(spec for spec in query_specs() if spec.query_id == "Q1"),),
        )
