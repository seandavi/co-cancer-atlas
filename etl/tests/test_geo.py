"""Unit tests for geo FeatureCollection assembly.

The TopoJSON conversion now goes through `npx mapshaper`; we don't
test that here. The `verify.py` end-to-end check confirms the round-
trip on the live snapshot.
"""

from __future__ import annotations

import json

from co_cancer_atlas_etl.geo import (
    counties_to_feature_collection,
    tracts_to_feature_collection,
)


_GEOM_A = json.dumps(
    {
        "type": "Polygon",
        "coordinates": [[[-105.0, 40.0], [-104.5, 40.0], [-104.5, 40.5], [-105.0, 40.0]]],
    }
)
_GEOM_B = json.dumps(
    {
        "type": "Polygon",
        "coordinates": [[[-104.5, 40.0], [-104.0, 40.0], [-104.0, 40.5], [-104.5, 40.0]]],
    }
)


def test_counties_use_us_fips_as_feature_id() -> None:
    fc = counties_to_feature_collection(
        [
            {"wkb_geometry": _GEOM_A, "us_fips": "08001", "full": "Adams County"},
            {"wkb_geometry": _GEOM_B, "us_fips": "08031", "full": "Denver County"},
        ]
    )
    assert fc["type"] == "FeatureCollection"
    assert [f["id"] for f in fc["features"]] == ["08001", "08031"]
    assert fc["features"][1]["properties"]["name"] == "Denver County"
    assert fc["features"][1]["properties"]["fips"] == "08031"


def test_counties_skip_rows_missing_fips_or_geometry() -> None:
    fc = counties_to_feature_collection(
        [
            {"wkb_geometry": _GEOM_A, "us_fips": "08001", "full": "Adams"},
            {"wkb_geometry": None, "us_fips": "08099", "full": "Prowers"},
            {"wkb_geometry": _GEOM_B, "us_fips": None, "full": "no fips"},
        ]
    )
    assert [f["id"] for f in fc["features"]] == ["08001"]


def test_counties_falls_back_through_name_fields() -> None:
    fc = counties_to_feature_collection(
        [{"wkb_geometry": _GEOM_A, "us_fips": "08001", "label": "Adams"}]
    )
    assert fc["features"][0]["properties"]["name"] == "Adams"


def test_tracts_use_fips_as_feature_id() -> None:
    fc = tracts_to_feature_collection(
        [
            {"wkb_geometry": _GEOM_A, "fips": "08031001000"},
            {"wkb_geometry": _GEOM_B, "fips": "08031002000"},
        ]
    )
    assert [f["id"] for f in fc["features"]] == ["08031001000", "08031002000"]
    assert fc["features"][0]["properties"]["name"] == "08031001000"
