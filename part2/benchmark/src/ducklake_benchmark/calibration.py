import math
import random
import shutil
import statistics
import time
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Callable

import duckdb

from .queries import QuerySpec


@dataclass(frozen=True)
class CalibrationConfig:
    warmups: int = 2
    initial_pairs: int = 7
    pair_increment: int = 4
    max_pairs: int = 19
    bootstrap_samples: int = 2_000
    precision_ratio: float = 1.05

    def __post_init__(self) -> None:
        if self.warmups < 0:
            raise ValueError("warmups must be non-negative")
        if self.initial_pairs <= 0:
            raise ValueError("initial_pairs must be positive")
        if self.pair_increment <= 0:
            raise ValueError("pair_increment must be positive")
        if self.max_pairs < self.initial_pairs:
            raise ValueError("max_pairs must be at least initial_pairs")
        if self.bootstrap_samples <= 0:
            raise ValueError("bootstrap_samples must be positive")
        if self.precision_ratio <= 1:
            raise ValueError("precision_ratio must be greater than one")


@dataclass(frozen=True)
class PairedObservation:
    block: int
    first_lane: str
    lane_1_ns: int
    lane_2_ns: int


@dataclass(frozen=True)
class CalibrationLanes:
    lane_1_path: Path
    lane_2_path: Path
    lane_1_sha256: str
    lane_2_sha256: str


@dataclass(frozen=True)
class RemoteCalibrationLanes:
    lane_1_uri: str
    lane_2_uri: str
    lane_1_sha256: str
    lane_2_sha256: str
    physical_bytes_per_lane: int


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_identical_lanes(source_path: Path, output_dir: Path) -> CalibrationLanes:
    if output_dir.exists():
        raise FileExistsError(f"calibration lane output already exists: {output_dir}")
    lane_1_path = output_dir / "a0_lane_1" / "data.parquet"
    lane_2_path = output_dir / "a0_lane_2" / "data.parquet"
    lane_1_path.parent.mkdir(parents=True)
    lane_2_path.parent.mkdir(parents=True)
    shutil.copyfile(source_path, lane_1_path)
    shutil.copyfile(source_path, lane_2_path)
    return CalibrationLanes(
        lane_1_path=lane_1_path,
        lane_2_path=lane_2_path,
        lane_1_sha256=_file_sha256(lane_1_path),
        lane_2_sha256=_file_sha256(lane_2_path),
    )


def _quantile(values: list[float] | list[int], probability: float) -> float:
    if not values:
        raise ValueError("at least one value is required")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return float(ordered[lower_index])
    weight = position - lower_index
    return float(ordered[lower_index] * (1 - weight) + ordered[upper_index] * weight)


def _bootstrap_median_ci(
    values: list[float], *, samples: int, seed: int
) -> tuple[float, float]:
    generator = random.Random(seed)
    medians = [
        statistics.median(generator.choices(values, k=len(values))) for _ in range(samples)
    ]
    return _quantile(medians, 0.025), _quantile(medians, 0.975)


def summarise_observations(
    observations: list[PairedObservation], *, bootstrap_samples: int, seed: int
) -> dict[str, object]:
    if not observations:
        raise ValueError("at least one paired observation is required")
    if any(item.lane_1_ns <= 0 or item.lane_2_ns <= 0 for item in observations):
        raise ValueError("durations must be positive")

    lane_1 = [item.lane_1_ns for item in observations]
    lane_2 = [item.lane_2_ns for item in observations]
    log_gains = [math.log(left / right) for left, right in zip(lane_1, lane_2, strict=True)]
    median_log_gain = statistics.median(log_gains)
    ci_lower, ci_upper = _bootstrap_median_ci(
        log_gains, samples=bootstrap_samples, seed=seed
    )
    noise_bound_log = _quantile([abs(value) for value in log_gains], 0.95)

    def lane_summary(values: list[int]) -> dict[str, float]:
        return {
            "median_ns": statistics.median(values),
            "iqr_ns": _quantile(values, 0.75) - _quantile(values, 0.25),
        }

    return {
        "pair_count": len(observations),
        "observations": [asdict(item) for item in observations],
        "lane_1": lane_summary(lane_1),
        "lane_2": lane_summary(lane_2),
        "paired_log_gains": log_gains,
        "median_paired_log_gain": median_log_gain,
        "bootstrap_95_ci_log": {"lower": ci_lower, "upper": ci_upper},
        "bootstrap_95_ci_half_width_log": max(
            median_log_gain - ci_lower, ci_upper - median_log_gain
        ),
        "noise_bound_log": noise_bound_log,
        "noise_bound_ratio": math.exp(noise_bound_log),
    }


def _measure_query(
    connection: duckdb.DuckDBPyConnection, sql: str
) -> tuple[int, list[tuple[object, ...]]]:
    started = time.perf_counter_ns()
    rows = connection.execute(sql).fetchall()
    return time.perf_counter_ns() - started, rows


def calibrate_parquet_pair(
    lanes: CalibrationLanes,
    *,
    config: CalibrationConfig,
    seed: int,
    specs: tuple[QuerySpec, ...],
) -> dict[str, object]:
    byte_identical = (
        lanes.lane_1_sha256 == lanes.lane_2_sha256
        and lanes.lane_1_path.stat().st_size == lanes.lane_2_path.stat().st_size
    )
    if not byte_identical:
        raise ValueError("A0 calibration lanes must be byte-identical")

    return _calibrate_parquet_sources(
        lane_1_source=str(lanes.lane_1_path),
        lane_2_source=str(lanes.lane_2_path),
        lane_1_sha256=lanes.lane_1_sha256,
        lane_2_sha256=lanes.lane_2_sha256,
        physical_bytes_per_lane=lanes.lane_1_path.stat().st_size,
        connection_setup=None,
        config=config,
        seed=seed,
        specs=specs,
    )


def calibrate_parquet_uris(
    lanes: RemoteCalibrationLanes,
    *,
    config: CalibrationConfig,
    seed: int,
    specs: tuple[QuerySpec, ...],
    connection_setup: Callable[[duckdb.DuckDBPyConnection], None] | None = None,
) -> dict[str, object]:
    if lanes.lane_1_sha256 != lanes.lane_2_sha256 or lanes.physical_bytes_per_lane <= 0:
        raise ValueError("A0 calibration lanes must be byte-identical")
    return _calibrate_parquet_sources(
        lane_1_source=lanes.lane_1_uri,
        lane_2_source=lanes.lane_2_uri,
        lane_1_sha256=lanes.lane_1_sha256,
        lane_2_sha256=lanes.lane_2_sha256,
        physical_bytes_per_lane=lanes.physical_bytes_per_lane,
        connection_setup=connection_setup,
        config=config,
        seed=seed,
        specs=specs,
    )


def _calibrate_parquet_sources(
    *,
    lane_1_source: str,
    lane_2_source: str,
    lane_1_sha256: str,
    lane_2_sha256: str,
    physical_bytes_per_lane: int,
    connection_setup: Callable[[duckdb.DuckDBPyConnection], None] | None,
    config: CalibrationConfig,
    seed: int,
    specs: tuple[QuerySpec, ...],
) -> dict[str, object]:
    byte_identical = lane_1_sha256 == lane_2_sha256

    escaped_lane_1 = lane_1_source.replace("'", "''")
    escaped_lane_2 = lane_2_source.replace("'", "''")
    query_results: dict[str, object] = {}
    precision_target = math.log(config.precision_ratio)

    for spec in specs:
        query_seed = seed + sum(ord(character) for character in spec.query_id)
        generator = random.Random(query_seed)
        lane_sql = {
            "lane_1": spec.sql_template.format(relation="lane_1"),
            "lane_2": spec.sql_template.format(relation="lane_2"),
        }
        with duckdb.connect() as connection:
            if connection_setup is not None:
                connection_setup(connection)
            connection.execute(
                f"CREATE TEMP VIEW lane_1 AS SELECT * FROM read_parquet('{escaped_lane_1}')"
            )
            connection.execute(
                f"CREATE TEMP VIEW lane_2 AS SELECT * FROM read_parquet('{escaped_lane_2}')"
            )

            first_order = ["lane_1", "lane_2"]
            generator.shuffle(first_order)
            first_durations: dict[str, int] = {}
            first_results: dict[str, list[tuple[object, ...]]] = {}
            for lane in first_order:
                duration, rows = _measure_query(connection, lane_sql[lane])
                first_durations[lane] = duration
                first_results[lane] = rows
            result_match = first_results["lane_1"] == first_results["lane_2"]
            if not result_match:
                raise ValueError(f"query {spec.query_id} returned different lane results")

            for _ in range(config.warmups):
                warmup_order = ["lane_1", "lane_2"]
                generator.shuffle(warmup_order)
                for lane in warmup_order:
                    connection.execute(lane_sql[lane]).fetchall()

            observations: list[PairedObservation] = []
            target_pairs = config.initial_pairs
            summary: dict[str, object]
            while True:
                while len(observations) < target_pairs:
                    order = ["lane_1", "lane_2"]
                    generator.shuffle(order)
                    durations: dict[str, int] = {}
                    for lane in order:
                        durations[lane], _ = _measure_query(connection, lane_sql[lane])
                    observations.append(
                        PairedObservation(
                            block=len(observations) + 1,
                            first_lane=order[0],
                            lane_1_ns=durations["lane_1"],
                            lane_2_ns=durations["lane_2"],
                        )
                    )
                summary = summarise_observations(
                    observations,
                    bootstrap_samples=config.bootstrap_samples,
                    seed=query_seed + len(observations),
                )
                if (
                    summary["bootstrap_95_ci_half_width_log"] <= precision_target
                    or len(observations) >= config.max_pairs
                ):
                    break
                target_pairs = min(
                    len(observations) + config.pair_increment, config.max_pairs
                )

        precision_sufficient = summary["bootstrap_95_ci_half_width_log"] <= precision_target
        query_results[spec.query_id] = {
            "category": spec.category,
            "primary": spec.primary,
            "first_query": {
                "order": first_order,
                "lane_1_ns": first_durations["lane_1"],
                "lane_2_ns": first_durations["lane_2"],
                "result_match": result_match,
            },
            "warmup_count_per_lane": config.warmups,
            **summary,
            "precision_target_half_width_log": precision_target,
            "precision_sufficient": precision_sufficient,
            "status": "stable" if precision_sufficient else "unstable_at_pair_cap",
        }

    return {
        "calibration_version": 1,
        "timer": "time.perf_counter_ns",
        "config": asdict(config),
        "lane_integrity": {
            "byte_identical": byte_identical,
            "lane_1_sha256": lane_1_sha256,
            "lane_2_sha256": lane_2_sha256,
            "physical_bytes_per_lane": physical_bytes_per_lane,
        },
        "queries": query_results,
    }
