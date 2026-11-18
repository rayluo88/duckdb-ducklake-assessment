import pytest

from ducklake_benchmark.model import Scenario, SyntheticProfile


def test_profile_manifest_labels_synthetic_parameters_as_assumed() -> None:
    profile = SyntheticProfile(rows=1_000, seed=7, scenario=Scenario.SHUFFLED)

    assert profile.to_manifest() == {
        "rows": {"value": 1_000, "provenance": "assumed"},
        "seed": {"value": 7, "provenance": "assumed"},
        "scenario": {"value": "shuffled", "provenance": "assumed"},
    }


@pytest.mark.parametrize("rows", [0, -1])
def test_profile_rejects_non_positive_rows(rows: int) -> None:
    with pytest.raises(ValueError, match="rows must be positive"):
        SyntheticProfile(rows=rows)
