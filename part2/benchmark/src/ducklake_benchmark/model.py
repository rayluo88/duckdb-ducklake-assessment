from dataclasses import dataclass
from enum import StrEnum


class Scenario(StrEnum):
    SHUFFLED = "shuffled"
    MODERATELY_CORRELATED = "moderately_correlated"
    STRONGLY_CLUSTERED = "strongly_clustered"


@dataclass(frozen=True)
class SyntheticProfile:
    rows: int
    seed: int = 20260809
    scenario: Scenario = Scenario.SHUFFLED

    def __post_init__(self) -> None:
        if self.rows <= 0:
            raise ValueError("rows must be positive")

    def to_manifest(self) -> dict[str, dict[str, int | str]]:
        return {
            "rows": {"value": self.rows, "provenance": "assumed"},
            "seed": {"value": self.seed, "provenance": "assumed"},
            "scenario": {"value": self.scenario.value, "provenance": "assumed"},
        }
