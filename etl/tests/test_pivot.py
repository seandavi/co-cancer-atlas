"""Unit tests for pivot factor enumeration and measure_id construction."""

from __future__ import annotations

from co_cancer_atlas_etl.pivot import (
    _enumerate_factor_combos,
    _measure_id_for_combo,
    _non_default_subset,
)


_SCP_FACTORS = {
    "sex": {"label": "Sex", "default": "All", "values": {"All": "All", "Female": "Female", "Male": "Male"}},
    "stage": {"label": "Stage", "default": "All Stages", "values": {"All Stages": "All Stages"}},
    "race": {
        "label": "Race",
        "default": "All Races (includes Hispanic)",
        "values": {
            "All Races (includes Hispanic)": "x",
            "Black (Non-Hispanic)": "x",
        },
    },
}


def test_enumerate_no_factors() -> None:
    assert _enumerate_factor_combos({}) == [{}]


def test_enumerate_cartesian_product() -> None:
    combos = _enumerate_factor_combos(_SCP_FACTORS)
    # 3 sex × 1 stage × 2 race
    assert len(combos) == 6
    # Every combo is a dict over all factor keys
    for c in combos:
        assert set(c.keys()) == {"sex", "stage", "race"}


def test_non_default_subset_strips_defaults() -> None:
    primary = {"sex": "All", "stage": "All Stages", "race": "All Races (includes Hispanic)"}
    assert _non_default_subset(_SCP_FACTORS, primary) == {}

    mixed = {"sex": "Female", "stage": "All Stages", "race": "Black (Non-Hispanic)"}
    assert _non_default_subset(_SCP_FACTORS, mixed) == {
        "sex": "Female",
        "race": "Black (Non-Hispanic)",
    }


def test_measure_id_primary_when_all_defaults() -> None:
    primary = {"sex": "All", "stage": "All Stages", "race": "All Races (includes Hispanic)"}
    assert (
        _measure_id_for_combo(
            "scpincidence", "All Cancer Sites", _SCP_FACTORS, primary
        )
        == "scpincidence.All Cancer Sites"
    )


def test_measure_id_suffix_when_non_default() -> None:
    combo = {"sex": "Female", "stage": "All Stages", "race": "Black (Non-Hispanic)"}
    assert (
        _measure_id_for_combo(
            "scpincidence", "All Cancer Sites", _SCP_FACTORS, combo
        )
        == "scpincidence.All Cancer Sites#race=Black (Non-Hispanic);sex=Female"
    )


def test_factorless_measure_yields_primary_id() -> None:
    assert (
        _measure_id_for_combo("sociodemographics", "Total Population", {}, {})
        == "sociodemographics.Total Population"
    )
