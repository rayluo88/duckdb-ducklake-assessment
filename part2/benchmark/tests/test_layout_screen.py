from __future__ import annotations

import math

import pytest

from ducklake_benchmark.layout_screen import PRIMARY_QUERY_IDS
from ducklake_benchmark.layout_screen import Q0Observation
from ducklake_benchmark.layout_screen import ScreenObservation
from ducklake_benchmark.layout_screen import build_schedule
from ducklake_benchmark.layout_screen import campaign_outcome
from ducklake_benchmark.layout_screen import evaluate_eligibility
from ducklake_benchmark.layout_screen import run_paired_screen
from ducklake_benchmark.layout_screen import run_paired_relation_screen
from ducklake_benchmark.layout_screen import summarise_noise
from ducklake_benchmark.layout_screen import summarise_screen
from ducklake_benchmark.fixture import build_local_fixture
from ducklake_benchmark.layouts import generate_layout
from ducklake_benchmark.layouts import layout_specs
from ducklake_benchmark.model import Scenario
from ducklake_benchmark.model import SyntheticProfile


def _observations(*, multiplier: float = 1.0) -> list[ScreenObservation]:
    return [
        ScreenObservation(
            block=block,
            query_id=query_id,
            first_representation="A0",
            a0_ns=100,
            candidate_ns=round(100 / multiplier),
            result_match=True,
        )
        for block in (1, 2, 3)
        for query_id in PRIMARY_QUERY_IDS
    ]


def _q0_observations() -> list[Q0Observation]:
    return [Q0Observation(block=block, a0_ns=100, candidate_ns=100, result_match=True) for block in (1, 2, 3)]


def test_screen_summary_uses_equal_weight_shared_blocks_and_deterministic_bootstrap() -> None:
    summary = summarise_screen(
        _observations(multiplier=2.0),
        variant_id="B1",
        bootstrap_samples=200,
        q0_observations=_q0_observations(),
    )

    assert summary["point_estimate_log"] == pytest.approx(math.log(2))
    assert summary["block_scores_log"] == pytest.approx([math.log(2)] * 3)
    assert summary["bootstrap_95_ci_log"] == {
        "lower": pytest.approx(math.log(2)),
        "upper": pytest.approx(math.log(2)),
    }
    assert summary == summarise_screen(
        _observations(multiplier=2.0),
        variant_id="B1",
        bootstrap_samples=200,
        q0_observations=_q0_observations(),
    )


def test_screen_summary_requires_exactly_one_primary_cell_per_block() -> None:
    observations = _observations()
    observations.pop()

    with pytest.raises(ValueError, match="all eight primary query cells"):
        summarise_screen(
            observations, variant_id="B1", bootstrap_samples=10, q0_observations=_q0_observations()
        )


def test_noise_and_q3_veto_use_predeclared_comparisons() -> None:
    noise = summarise_noise(
        _observations(multiplier=1.0), bootstrap_samples=100, q0_observations=_q0_observations()
    )
    candidate = summarise_screen(
        _observations(multiplier=1.1),
        variant_id="B2",
        bootstrap_samples=100,
        q0_observations=_q0_observations(),
    )

    eligibility = evaluate_eligibility(
        correctness_valid=True,
        summary=candidate,
        noise=noise,
    )

    assert noise["aggregate_noise_log"] == pytest.approx(0.0)
    assert noise["q3_noise_log"] == pytest.approx(0.0)
    assert eligibility["status"] == "eligible"
    assert eligibility["aggregate_effect_pass"] is True
    assert eligibility["q3_veto_pass"] is True


def test_q0_is_required_raw_evidence_but_does_not_change_selection_statistics() -> None:
    primary = _observations(multiplier=1.1)
    q0_fast = [Q0Observation(block=block, a0_ns=100, candidate_ns=1, result_match=True) for block in (1, 2, 3)]
    q0_slow = [Q0Observation(block=block, a0_ns=100, candidate_ns=10_000, result_match=True) for block in (1, 2, 3)]

    fast_summary = summarise_screen(
        primary, variant_id="B3", bootstrap_samples=100, q0_observations=q0_fast
    )
    slow_summary = summarise_screen(
        primary, variant_id="B3", bootstrap_samples=100, q0_observations=q0_slow
    )

    assert fast_summary["point_estimate_log"] == slow_summary["point_estimate_log"]
    assert fast_summary["q0"] != slow_summary["q0"]

    with pytest.raises(ValueError, match="Q0 observation"):
        summarise_screen(primary, variant_id="B3", bootstrap_samples=100, q0_observations=[])


def test_campaign_outcome_distinguishes_bounded_null_from_inconclusive() -> None:
    assert campaign_outcome({"B1": "valid_not_eligible", "B2": "valid_not_eligible", "B3": "valid_not_eligible"}) == "bounded_null"
    assert campaign_outcome({"B1": "eligible", "B2": "unsupported", "B3": "valid_not_eligible"}) == "inconclusive"


def test_fixed_schedule_has_checked_block_major_query_and_lane_order() -> None:
    schedule = build_schedule(variant_id="B1", blocks=2)

    assert [(item.block, item.query_id, item.first_representation) for item in schedule] == [
        (1, "Q6", "candidate"),
        (1, "Q2a", "A0"),
        (1, "Q3", "candidate"),
        (1, "Q2c", "A0"),
        (1, "Q5", "A0"),
        (1, "Q2b", "A0"),
        (1, "Q1", "A0"),
        (1, "Q4", "A0"),
        (2, "Q5", "A0"),
        (2, "Q3", "A0"),
        (2, "Q2c", "candidate"),
        (2, "Q4", "A0"),
        (2, "Q2a", "A0"),
        (2, "Q2b", "candidate"),
        (2, "Q6", "candidate"),
        (2, "Q1", "A0"),
    ]


def test_paired_runner_keeps_q0_diagnostic_and_completes_block_major_fixture(tmp_path) -> None:
    fixture = build_local_fixture(
        tmp_path / "fixture",
        SyntheticProfile(rows=2_000, seed=25, scenario=Scenario.SHUFFLED),
    )
    layout_dir = tmp_path / "B1"
    manifest = generate_layout(
        layout_specs()[0],
        database_path=fixture.database_path,
        a0_path=fixture.parquet_path,
        source_table="synthetic_data",
        output_dir=layout_dir,
    )
    result = run_paired_screen(
        a0_files=[fixture.parquet_path],
        candidate_files=[layout_dir / file["path"] for file in manifest["files"]],
        variant_id="B1",
        blocks=3,
        warmups=1,
        bootstrap_samples=100,
    )

    assert result["runner"]["persistent_connections"] == 9
    assert result["runner"]["blocks"] == 3
    assert len(result["observations"]) == 3 * len(PRIMARY_QUERY_IDS)
    assert len(result["q0"]) == 3
    assert all(item["result_match"] is True for item in result["observations"])
    assert result["q0"][-1]["block"] == 3


def test_relation_runner_configures_all_nine_isolated_query_connections(tmp_path) -> None:
    fixture = build_local_fixture(
        tmp_path / "fixture",
        SyntheticProfile(rows=2_000, seed=26, scenario=Scenario.SHUFFLED),
    )
    relation = f"read_parquet('{fixture.parquet_path.as_posix()}')"
    configured: list[object] = []

    result = run_paired_relation_screen(
        a0_relation_sql=relation,
        candidate_relation_sql=relation,
        variant_id="A0_REMOTE_NOISE",
        blocks=3,
        warmups=1,
        bootstrap_samples=100,
        connection_setup=configured.append,
    )

    assert len(configured) == 9
    assert result["runner"]["persistent_connections"] == 9
    assert len(result["observations"]) == 3 * len(PRIMARY_QUERY_IDS)
