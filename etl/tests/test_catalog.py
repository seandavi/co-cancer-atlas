"""Unit tests for catalog.build_catalog and the measure_id helpers."""

from __future__ import annotations

import json

from co_cancer_atlas_etl.catalog import (
    NUMERIC_UNITS,
    build_catalog,
    factor_suffix,
    primary_measure_id,
)


def _sample_catalog() -> dict:
    return {
        "county": {
            "label": "County",
            "categories": {
                "sociodemographics": {
                    "label": "Sociodemographics",
                    "measures": {
                        "Total Population": {
                            "label": "Total Population",
                            "unit": "count",
                            "source": "ACS",
                            "source_url": "https://census.gov",
                            "factors": {},
                        },
                    },
                },
                "scpincidence": {
                    "label": "Cancer Incidence",
                    "measures": {
                        "All Cancer Sites": {
                            "label": "All Cancer Sites",
                            "unit": "rate",
                            "source": "SCP",
                            "source_url": "https://scp.cancer.gov",
                            "factors": {
                                "sex": {
                                    "label": "Sex",
                                    "default": "All",
                                    "values": {"All": "All", "Female": "Female"},
                                },
                            },
                        },
                    },
                },
                "disparities": {
                    "label": "Disparities Index",
                    "measures": {
                        "Risk Index": {
                            "label": "Risk Index",
                            "unit": "ordinal",
                            "source": "x",
                            "source_url": "x",
                            "factors": {},
                        },
                    },
                },
            },
        },
    }


def test_primary_measure_id() -> None:
    assert primary_measure_id("scpincidence", "All Cancer Sites") == (
        "scpincidence.All Cancer Sites"
    )


def test_factor_suffix_is_sorted_and_stable() -> None:
    # Different insertion order, same canonical suffix.
    a = factor_suffix({"sex": "Female", "race": "Black NH"})
    b = factor_suffix({"race": "Black NH", "sex": "Female"})
    assert a == b == "#race=Black NH;sex=Female"
    assert factor_suffix({}) == ""


def test_build_catalog_emits_one_row_per_measure() -> None:
    df = build_catalog(_sample_catalog())
    # 3 measures across 1 level
    assert df.height == 3
    rows = df.to_dicts()
    ids = {r["measure_id"] for r in rows}
    assert ids == {
        "sociodemographics.Total Population",
        "scpincidence.All Cancer Sites",
        "disparities.Risk Index",
    }


def test_build_catalog_is_numeric_flag() -> None:
    rows = build_catalog(_sample_catalog()).to_dicts()
    by_id = {r["measure_id"]: r for r in rows}
    assert by_id["sociodemographics.Total Population"]["is_numeric"] is True
    assert by_id["scpincidence.All Cancer Sites"]["is_numeric"] is True
    # ordinal is not numeric
    assert by_id["disparities.Risk Index"]["is_numeric"] is False


def test_build_catalog_carries_factors_json() -> None:
    rows = build_catalog(_sample_catalog()).to_dicts()
    by_id = {r["measure_id"]: r for r in rows}
    parsed = json.loads(by_id["scpincidence.All Cancer Sites"]["factors"])
    assert parsed["sex"]["default"] == "All"
    assert json.loads(by_id["sociodemographics.Total Population"]["factors"]) == {}


def test_build_catalog_is_deterministic() -> None:
    a = build_catalog(_sample_catalog()).to_dicts()
    b = build_catalog(_sample_catalog()).to_dicts()
    assert a == b
    # Sorted by (level, dataset, measure)
    keys = [(r["level"], r["dataset"], r["measure"]) for r in a]
    assert keys == sorted(keys)


def test_numeric_units_covers_spec_set() -> None:
    # SPEC §3 lists these four as the eligibility set for correlation/clustering.
    assert NUMERIC_UNITS == {"percent", "rate", "count", "dollar_amount"}
