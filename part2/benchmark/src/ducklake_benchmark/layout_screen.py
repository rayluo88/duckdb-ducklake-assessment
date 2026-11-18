from __future__ import annotations

import hashlib
import math
import os
import platform
import random
import statistics
import time
from collections.abc import Callable
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path

import duckdb

from .layout_correctness import render_read_parquet
from .queries import query_specs


PRIMARY_QUERY_IDS = ("Q1", "Q2a", "Q2b", "Q2c", "Q3", "Q4", "Q5", "Q6")
PRECISION_TARGET_LOG = math.log(1.05)


@dataclass(frozen=True)
class ScreenObservation:
    block: int
    query_id: str
    first_representation: str
    a0_ns: int
    candidate_ns: int
    result_match: bool


@dataclass(frozen=True)
class Q0Observation:
    block: int
    a0_ns: int
    candidate_ns: int
    result_match: bool


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("at least one value is required")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _bootstrap_median_ci(values: list[float], *, samples: int, seed: int) -> tuple[float, float]:
    if samples <= 0:
        raise ValueError("bootstrap_samples must be positive")
    generator = random.Random(seed)
    medians = [statistics.median(generator.choices(values, k=len(values))) for _ in range(samples)]
    return _quantile(medians, 0.025), _quantile(medians, 0.975)


def _validated_blocks(
    observations: list[ScreenObservation], q0_observations: list[Q0Observation]
) -> dict[int, dict[str, ScreenObservation]]:
    if not observations:
        raise ValueError("at least one primary observation is required")
    cells: dict[int, dict[str, ScreenObservation]] = {}
    for observation in observations:
        if observation.query_id not in PRIMARY_QUERY_IDS:
            raise ValueError(f"unknown primary query ID: {observation.query_id}")
        if not observation.result_match or observation.a0_ns <= 0 or observation.candidate_ns <= 0:
            raise ValueError("primary observations require matching positive results")
        per_block = cells.setdefault(observation.block, {})
        if observation.query_id in per_block:
            raise ValueError("all eight primary query cells must occur exactly once per block")
        per_block[observation.query_id] = observation
    if any(set(per_block) != set(PRIMARY_QUERY_IDS) for per_block in cells.values()):
        raise ValueError("all eight primary query cells must occur exactly once per block")
    q0_by_block = {observation.block: observation for observation in q0_observations}
    if len(q0_by_block) != len(q0_observations) or set(q0_by_block) != set(cells):
        raise ValueError("one matching Q0 observation is required for each block")
    if any(
        not observation.result_match or observation.a0_ns <= 0 or observation.candidate_ns <= 0
        for observation in q0_observations
    ):
        raise ValueError("Q0 observation requires matching positive results")
    return cells


def _q3_summary(gains_by_block: dict[int, dict[str, float]], *, bootstrap_samples: int, seed: int) -> dict[str, object]:
    gains = [gains_by_block[block]["Q3"] for block in sorted(gains_by_block)]
    point_estimate = statistics.median(gains)
    lower, upper = _bootstrap_median_ci(gains, samples=bootstrap_samples, seed=seed)
    half_width = max(point_estimate - lower, upper - point_estimate)
    return {
        "point_estimate_log": point_estimate,
        "bootstrap_95_ci_log": {"lower": lower, "upper": upper},
        "bootstrap_95_ci_half_width_log": half_width,
        "precision_stable": half_width <= PRECISION_TARGET_LOG,
    }


def summarise_screen(
    observations: list[ScreenObservation],
    *,
    variant_id: str,
    bootstrap_samples: int = 2_000,
    q0_observations: list[Q0Observation],
) -> dict[str, object]:
    """Summarise a fixed shared-block candidate screen without using Q0 for selection."""

    cells = _validated_blocks(observations, q0_observations)
    gains_by_block = {
        block: {
            query_id: math.log(cell.a0_ns / cell.candidate_ns)
            for query_id, cell in per_block.items()
        }
        for block, per_block in cells.items()
    }
    block_scores = [
        statistics.mean(gains_by_block[block][query_id] for query_id in PRIMARY_QUERY_IDS)
        for block in sorted(gains_by_block)
    ]
    point_estimate = statistics.median(block_scores)
    seed = 20260809 + sum(ord(character) for character in variant_id)
    lower, upper = _bootstrap_median_ci(block_scores, samples=bootstrap_samples, seed=seed)
    half_width = max(point_estimate - lower, upper - point_estimate)
    q0_by_block = {item.block: item for item in q0_observations}
    return {
        "variant_id": variant_id,
        "blocks": sorted(cells),
        "observations": [asdict(item) for item in observations],
        "q0": [asdict(q0_by_block[block]) for block in sorted(q0_by_block)],
        "block_scores_log": block_scores,
        "point_estimate_log": point_estimate,
        "bootstrap_95_ci_log": {"lower": lower, "upper": upper},
        "bootstrap_95_ci_half_width_log": half_width,
        "precision_stable": half_width <= PRECISION_TARGET_LOG,
        "q3": _q3_summary(
            gains_by_block,
            bootstrap_samples=bootstrap_samples,
            seed=seed + sum(ord(character) for character in "Q3"),
        ),
    }


def summarise_noise(
    observations: list[ScreenObservation],
    *,
    bootstrap_samples: int = 2_000,
    q0_observations: list[Q0Observation],
) -> dict[str, object]:
    """Derive aggregate and Q3 noise thresholds from an A0-versus-A0 shared-block control."""

    summary = summarise_screen(
        observations,
        variant_id="A0_NOISE",
        bootstrap_samples=bootstrap_samples,
        q0_observations=q0_observations,
    )
    q3_gains = [
        math.log(item["a0_ns"] / item["candidate_ns"])
        for item in summary["observations"]
        if item["query_id"] == "Q3"
    ]
    return {
        "aggregate_noise_log": _quantile([abs(score) for score in summary["block_scores_log"]], 0.95),
        "q3_noise_log": _quantile([abs(gain) for gain in q3_gains], 0.95),
        "summary": summary,
    }


def evaluate_eligibility(
    *, correctness_valid: bool, summary: dict[str, object], noise: dict[str, object]
) -> dict[str, object]:
    if not correctness_valid:
        return {"status": "invalid_correctness", "aggregate_effect_pass": False, "q3_veto_pass": False}
    q3 = summary["q3"]
    aggregate_precision = bool(summary["precision_stable"])
    q3_precision = bool(q3["precision_stable"])
    aggregate_effect = summary["bootstrap_95_ci_log"]["lower"] > noise["aggregate_noise_log"]
    q3_veto = q3["bootstrap_95_ci_log"]["lower"] >= -noise["q3_noise_log"]
    if not aggregate_precision or not q3_precision:
        status = "unstable"
    elif aggregate_effect and q3_veto:
        status = "eligible"
    else:
        status = "valid_not_eligible"
    return {
        "status": status,
        "aggregate_precision_pass": aggregate_precision,
        "aggregate_effect_pass": aggregate_effect,
        "q3_precision_pass": q3_precision,
        "q3_veto_pass": q3_veto,
    }


def campaign_outcome(statuses: dict[str, str]) -> str:
    if set(statuses) != {"B1", "B2", "B3"}:
        raise ValueError("campaign outcome requires B1, B2 and B3 statuses")
    if all(status == "valid_not_eligible" for status in statuses.values()):
        return "bounded_null"
    if any(status in {"unsupported", "invalid_correctness", "aborted_environment", "unstable"} for status in statuses.values()):
        return "inconclusive"
    return "eligible_factors_present"


@dataclass(frozen=True)
class ScheduledPair:
    block: int
    query_id: str
    first_representation: str


def _lane_order_seed(*, campaign_seed: int, variant_id: str, block: int, query_id: str) -> int:
    return int.from_bytes(
        hashlib.sha256(
            f"{campaign_seed}|{variant_id}|{block}|{query_id}|lane_order".encode()
        ).digest()[:8],
        "big",
    )


def build_schedule(
    *, variant_id: str, blocks: int = 19, campaign_seed: int = 20260809
) -> list[ScheduledPair]:
    if blocks <= 0:
        raise ValueError("blocks must be positive")
    schedule: list[ScheduledPair] = []
    for block in range(1, blocks + 1):
        query_order = list(PRIMARY_QUERY_IDS)
        random.Random(20260809 + block).shuffle(query_order)
        for query_id in query_order:
            lane_order = ["A0", "candidate"]
            random.Random(
                _lane_order_seed(
                    campaign_seed=campaign_seed,
                    variant_id=variant_id,
                    block=block,
                    query_id=query_id,
                )
            ).shuffle(lane_order)
            schedule.append(
                ScheduledPair(
                    block=block, query_id=query_id, first_representation=lane_order[0]
                )
            )
    return schedule


def _measure_query(
    connection: duckdb.DuckDBPyConnection, sql: str
) -> tuple[int, list[tuple[object, ...]]]:
    started = time.perf_counter_ns()
    rows = connection.execute(sql).fetchall()
    return time.perf_counter_ns() - started, rows


def _connection_snapshot() -> dict[str, object]:
    free_memory_bytes: int | None = None
    try:
        free_memory_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        pass
    try:
        load_average: tuple[float, float, float] | None = os.getloadavg()
    except OSError:
        load_average = None
    return {
        "process_id": os.getpid(),
        "load_average": load_average,
        "free_memory_bytes": free_memory_bytes,
        "platform": platform.platform(),
    }


def _configure_connection(
    a0_relation_sql: str,
    candidate_relation_sql: str,
    connection_setup: Callable[[duckdb.DuckDBPyConnection], None] | None = None,
) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute("SET threads = 8")
    if connection_setup is not None:
        connection_setup(connection)
    connection.execute(f"CREATE TEMP VIEW a0 AS SELECT * FROM {a0_relation_sql}")
    connection.execute(f"CREATE TEMP VIEW candidate AS SELECT * FROM {candidate_relation_sql}")
    return connection


def run_paired_screen(
    *,
    a0_files: list[Path],
    candidate_files: list[Path],
    variant_id: str,
    blocks: int = 19,
    warmups: int = 2,
    bootstrap_samples: int = 2_000,
    campaign_seed: int = 20260809,
) -> dict[str, object]:
    """Run a fixed block-major local paired screen with persistent per-query connections."""

    a0_relation_sql = render_read_parquet([path.resolve() for path in a0_files])
    candidate_relation_sql = render_read_parquet([path.resolve() for path in candidate_files])
    return run_paired_relation_screen(
        a0_relation_sql=a0_relation_sql,
        candidate_relation_sql=candidate_relation_sql,
        variant_id=variant_id,
        blocks=blocks,
        warmups=warmups,
        bootstrap_samples=bootstrap_samples,
        campaign_seed=campaign_seed,
    )


def run_paired_relation_screen(
    *,
    a0_relation_sql: str,
    candidate_relation_sql: str,
    variant_id: str,
    blocks: int = 19,
    warmups: int = 2,
    bootstrap_samples: int = 2_000,
    campaign_seed: int = 20260809,
    connection_setup: Callable[[duckdb.DuckDBPyConnection], None] | None = None,
) -> dict[str, object]:
    """Run the fixed screen over relations whose connections may need remote setup."""

    if warmups < 0:
        raise ValueError("warmups must be non-negative")
    specs_by_id = {spec.query_id: spec for spec in query_specs()}
    if set(PRIMARY_QUERY_IDS).difference(specs_by_id) or "Q0" not in specs_by_id:
        raise ValueError("the fixed Q0–Q6 query specification is incomplete")

    primary_connections: dict[str, duckdb.DuckDBPyConnection] = {}
    q0_connection: duckdb.DuckDBPyConnection | None = None
    started_monotonic_ns = time.monotonic_ns()
    try:
        for query_id in PRIMARY_QUERY_IDS:
            primary_connections[query_id] = _configure_connection(
                a0_relation_sql, candidate_relation_sql, connection_setup
            )
        q0_connection = _configure_connection(
            a0_relation_sql, candidate_relation_sql, connection_setup
        )
        for query_id, connection in primary_connections.items():
            spec = specs_by_id[query_id]
            for representation in ("A0", "candidate"):
                sql = spec.sql_template.format(
                    relation="a0" if representation == "A0" else "candidate"
                )
                for _ in range(warmups):
                    connection.execute(sql).fetchall()
        q0_spec = specs_by_id["Q0"]
        for representation in ("A0", "candidate"):
            sql = q0_spec.sql_template.format(
                relation="a0" if representation == "A0" else "candidate"
            )
            for _ in range(warmups):
                q0_connection.execute(sql).fetchall()

        observations: list[ScreenObservation] = []
        q0_observations: list[Q0Observation] = []
        schedule = build_schedule(
            variant_id=variant_id, blocks=blocks, campaign_seed=campaign_seed
        )
        for scheduled in schedule:
            connection = primary_connections[scheduled.query_id]
            spec = specs_by_id[scheduled.query_id]
            order = [scheduled.first_representation, "candidate" if scheduled.first_representation == "A0" else "A0"]
            durations: dict[str, int] = {}
            results: dict[str, list[tuple[object, ...]]] = {}
            for representation in order:
                sql = spec.sql_template.format(
                    relation="a0" if representation == "A0" else "candidate"
                )
                durations[representation], results[representation] = _measure_query(connection, sql)
            if results["A0"] != results["candidate"]:
                raise ValueError(f"query result mismatch: {scheduled.query_id} block {scheduled.block}")
            observations.append(
                ScreenObservation(
                    block=scheduled.block,
                    query_id=scheduled.query_id,
                    first_representation=scheduled.first_representation,
                    a0_ns=durations["A0"],
                    candidate_ns=durations["candidate"],
                    result_match=True,
                )
            )
            block_complete = len(
                [item for item in observations if item.block == scheduled.block]
            ) == len(PRIMARY_QUERY_IDS)
            if block_complete:
                lane_order = ["A0", "candidate"]
                random.Random(
                    _lane_order_seed(
                        campaign_seed=campaign_seed,
                        variant_id=variant_id,
                        block=scheduled.block,
                        query_id="Q0",
                    )
                ).shuffle(lane_order)
                q0_durations: dict[str, int] = {}
                q0_results: dict[str, list[tuple[object, ...]]] = {}
                for representation in lane_order:
                    sql = q0_spec.sql_template.format(
                        relation="a0" if representation == "A0" else "candidate"
                    )
                    q0_durations[representation], q0_results[representation] = _measure_query(
                        q0_connection, sql
                    )
                if q0_results["A0"] != q0_results["candidate"]:
                    raise ValueError(f"query result mismatch: Q0 block {scheduled.block}")
                q0_observations.append(
                    Q0Observation(
                        block=scheduled.block,
                        a0_ns=q0_durations["A0"],
                        candidate_ns=q0_durations["candidate"],
                        result_match=True,
                    )
                )
        summary = summarise_screen(
            observations,
            variant_id=variant_id,
            bootstrap_samples=bootstrap_samples,
            q0_observations=q0_observations,
        )
        return {
            **summary,
            "runner": {
                "blocks": blocks,
                "warmups_per_representation_per_query": warmups,
                "persistent_connections": len(primary_connections) + 1,
                "timer": "time.perf_counter_ns",
                "started_monotonic_ns": started_monotonic_ns,
                "completed_monotonic_ns": time.monotonic_ns(),
                "environment": _connection_snapshot(),
            },
        }
    finally:
        for connection in primary_connections.values():
            connection.close()
        if q0_connection is not None:
            q0_connection.close()
