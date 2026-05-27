"""Unit tests for the SCP adapter — no network."""

from __future__ import annotations

import json

import polars as pl

from co_cancer_atlas_etl.scp import (
    LONG_SCHEMA,
    _build_catalog_rows,
    _build_long_rows,
    _factor_universe,
    _normalize_factors,
    normalize_sex,
)


def _scp_frame(rows: list[dict]) -> pl.DataFrame:
    """Build a fixture frame mimicking _read_release_csv output."""
    defaults: dict[str, object] = {
        "fips": "08001",
        "reported_locale": "Somewhere County, CO",
        "2023_rural_urban_continuum_codesrural_urban_note": "Urban",
        "age_adjusted_rate_per_100_000": 100.0,
        "lower_ci_rate": 90.0,
        "upper_ci_rate": 110.0,
        "average_annual_count": 50.0,
        "recent_trend": "stable",
        "recent_5_year_trend_in_rate": 0.1,
        "lower_ci_trend_in_rate": -0.2,
        "upper_ci_trend_in_rate": 0.5,
        "sex": "Both Sexes",
        "stage": "All Stages",
        "race": "All Races (includes Hispanic)",
        "cancer": "All Cancer Sites",
        "age": "All Ages",
        "state_fips": "08",
        "percent_of_cases_with_late_stage": None,
    }
    return pl.DataFrame(
        [{**defaults, **r} for r in rows],
        # Let polars infer schema; production code goes through TRY_CAST
        # in DuckDB so float columns arrive typed correctly.
    )


def test_normalize_sex_maps_both_sexes_to_all() -> None:
    assert normalize_sex("Both Sexes") == "All"
    assert normalize_sex("Female") == "Female"
    assert normalize_sex("Male") == "Male"
    # Unknown values pass through (be lenient — future SCP additions
    # shouldn't blow up the snapshot).
    assert normalize_sex("Unknown") == "Unknown"


def test_normalize_factors_applies_to_sex_column() -> None:
    df = _scp_frame(
        [
            {"sex": "Both Sexes"},
            {"sex": "Female"},
            {"sex": "Male"},
        ]
    )
    normalized = _normalize_factors(df)
    assert sorted(normalized.get_column("sex").unique().to_list()) == [
        "All",
        "Female",
        "Male",
    ]


def test_factor_universe_uses_global_default_when_present() -> None:
    df = _normalize_factors(
        _scp_frame(
            [
                {"cancer": "All Cancer Sites", "sex": "Both Sexes"},
                {"cancer": "All Cancer Sites", "sex": "Male"},
                {"cancer": "All Cancer Sites", "sex": "Female"},
            ]
        )
    )
    universe = _factor_universe(df)
    assert universe["All Cancer Sites"]["sex"]["default"] == "All"
    assert set(universe["All Cancer Sites"]["sex"]["values"]) == {
        "All",
        "Male",
        "Female",
    }


def test_factor_universe_falls_back_for_sex_specific_cancer() -> None:
    """Cervix only has Female rows — fallback to the observed value."""
    df = _normalize_factors(
        _scp_frame(
            [
                {"cancer": "Cervix", "sex": "Female"},
            ]
        )
    )
    universe = _factor_universe(df)
    # Global default "All" isn't observed; fallback uses the only
    # observed value.
    assert universe["Cervix"]["sex"]["default"] == "Female"
    assert universe["Cervix"]["sex"]["values"] == {"Female": "Female"}


def test_build_long_rows_uses_per_cancer_default_for_primary_id() -> None:
    df = _normalize_factors(
        _scp_frame(
            [
                {"cancer": "Cervix", "sex": "Female", "fips": "08031"},
            ]
        )
    )
    universe = _factor_universe(df)
    defaults_by_cancer = {
        cancer: {axis: factors[axis]["default"] for axis in factors}
        for cancer, factors in universe.items()
    }
    long_df = _build_long_rows(df, "scpincidence", defaults_by_cancer)
    # The single Cervix row should land on the primary measure_id —
    # no #sex=Female suffix, because sex=Female *is* the per-cancer
    # default for Cervix.
    assert long_df.get_column("measure_id").to_list() == [
        "scpincidence.Cervix"
    ]


def test_build_long_rows_emits_suffix_for_non_default_combo() -> None:
    # Include the default rows so factor_universe sees both axes as
    # multi-valued and picks the global defaults; otherwise the
    # observed-value fallback would treat the lone non-default combo as
    # default and emit no suffix.
    df = _normalize_factors(
        _scp_frame(
            [
                {
                    "cancer": "All Cancer Sites",
                    "sex": "Both Sexes",
                    "race": "All Races (includes Hispanic)",
                    "fips": "08031",
                },
                {
                    "cancer": "All Cancer Sites",
                    "sex": "Female",
                    "race": "Black (Non-Hispanic)",
                    "fips": "08001",
                },
            ]
        )
    )
    universe = _factor_universe(df)
    defaults_by_cancer = {
        cancer: {axis: factors[axis]["default"] for axis in factors}
        for cancer, factors in universe.items()
    }
    long_df = _build_long_rows(df, "scpincidence", defaults_by_cancer)
    measure_ids = sorted(long_df.get_column("measure_id").to_list())
    assert measure_ids == [
        "scpincidence.All Cancer Sites",
        "scpincidence.All Cancer Sites#race=Black (Non-Hispanic);sex=Female",
    ]


def test_build_long_rows_carries_ci_and_trend_columns() -> None:
    df = _normalize_factors(
        _scp_frame(
            [
                {
                    "cancer": "All Cancer Sites",
                    "lower_ci_rate": 393.5,
                    "age_adjusted_rate_per_100_000": 400.3,
                    "upper_ci_rate": 407.2,
                    "recent_trend": "falling",
                    "recent_5_year_trend_in_rate": -1.0,
                    "lower_ci_trend_in_rate": -1.6,
                    "upper_ci_trend_in_rate": -0.5,
                }
            ]
        )
    )
    universe = _factor_universe(df)
    defaults_by_cancer = {
        cancer: {axis: factors[axis]["default"] for axis in factors}
        for cancer, factors in universe.items()
    }
    long_df = _build_long_rows(df, "scpincidence", defaults_by_cancer)
    row = long_df.row(0, named=True)
    assert row["value"] == 400.3
    assert row["value_lo"] == 393.5
    assert row["value_hi"] == 407.2
    assert row["trend_str"] == "falling"
    assert row["trend_pct"] == -1.0
    assert row["trend_pct_lo"] == -1.6
    assert row["trend_pct_hi"] == -0.5
    # Schema columns are all present (extension cols even when null).
    assert set(long_df.columns) == set(LONG_SCHEMA)


def test_catalog_row_factors_json_roundtrips() -> None:
    df = _normalize_factors(
        _scp_frame(
            [
                {"cancer": "All Cancer Sites", "sex": "Both Sexes"},
                {"cancer": "All Cancer Sites", "sex": "Female"},
                {"cancer": "Cervix", "sex": "Female"},
            ]
        )
    )
    universe = _factor_universe(df)
    catalog_rows = _build_catalog_rows(df, "scpincidence", universe)
    by_id = {r["measure_id"]: r for r in catalog_rows}
    parsed = json.loads(by_id["scpincidence.All Cancer Sites"]["factors"])
    assert parsed["sex"]["default"] == "All"
    assert by_id["scpincidence.Cervix"]["unit"] == "rate"
    assert by_id["scpincidence.Cervix"]["is_numeric"] is True
    assert by_id["scpincidence.Cervix"]["source"] == "State Cancer Profiles (NCI)"
