"""Unit tests for individual verify.* checks (no network)."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from co_cancer_atlas_etl import verify


def _catalog(units: list[str], is_numeric: list[bool]) -> pl.DataFrame:
    n = len(units)
    return pl.DataFrame(
        {
            "measure_id": [f"d.m{i}" for i in range(n)],
            "dataset": ["d"] * n,
            "dataset_label": ["D"] * n,
            "level": ["county"] * n,
            "category": ["D"] * n,
            "measure": [f"m{i}" for i in range(n)],
            "label": [f"m{i}" for i in range(n)],
            "unit": units,
            "source": [""] * n,
            "source_url": [""] * n,
            "state_value": [None] * n,
            "factors": ["{}"] * n,
            "is_numeric": is_numeric,
        }
    )


_LONG_SCHEMA = {
    "fips": pl.Utf8,
    "measure_id": pl.Utf8,
    "value": pl.Float64,
    "value_str": pl.Utf8,
    "aac": pl.Float64,
}


def test_catalog_unit_enum_passes() -> None:
    cat = _catalog(["percent", "rate", "ordinal"], [True, True, False])
    assert verify.check_catalog_units(cat).passed


def test_catalog_unit_enum_fails_on_bogus_unit() -> None:
    cat = _catalog(["percent", "frobnitz"], [True, False])
    assert not verify.check_catalog_units(cat).passed


def test_is_numeric_matches_unit() -> None:
    cat = _catalog(["percent", "ordinal"], [True, False])
    assert verify.check_is_numeric_matches_unit(cat).passed

    bad = _catalog(["percent", "ordinal"], [False, False])
    assert not verify.check_is_numeric_matches_unit(bad).passed


def test_county_wide_row_count() -> None:
    df = pl.DataFrame({"fips": ["08001"] * 64, "name": ["x"] * 64})
    assert verify.check_county_wide_64_rows(df).passed

    df2 = pl.DataFrame({"fips": ["08001"] * 60, "name": ["x"] * 60})
    assert not verify.check_county_wide_64_rows(df2).passed


def test_fips_leading_zero_check() -> None:
    good = pl.DataFrame({"fips": ["08001", "08031", "08099"], "name": ["a", "b", "c"]})
    assert verify.check_fips_leading_zeros_preserved(good, "test").passed

    bad = pl.DataFrame({"fips": [8001, 8031], "name": ["a", "b"]})
    assert not verify.check_fips_leading_zeros_preserved(bad, "test").passed


def test_long_measure_ids_in_catalog() -> None:
    cat = _catalog(["percent", "rate"], [True, True])
    good_long = pl.DataFrame(
        {
            "fips": ["08001"] * 3,
            "measure_id": ["d.m0", "d.m1", "d.m1#sex=Female"],
            "value": [1.0, 2.0, 3.0],
            "value_str": [None] * 3,
            "aac": [None] * 3,
        },
        schema=_LONG_SCHEMA,
    )
    assert verify.check_long_measure_ids_in_catalog(good_long, cat, "long").passed

    bad_long = pl.DataFrame(
        {
            "fips": ["08001"],
            "measure_id": ["d.unknown"],
            "value": [1.0],
            "value_str": [None],
            "aac": [None],
        },
        schema=_LONG_SCHEMA,
    )
    assert not verify.check_long_measure_ids_in_catalog(bad_long, cat, "long").passed


def test_wide_columns_check() -> None:
    cat = _catalog(["percent", "rate", "ordinal"], [True, True, False])
    good_wide = pl.DataFrame(
        {"fips": ["08001"], "name": ["x"], "d.m0": [1.0], "d.m1": [2.0]}
    )
    assert verify.check_wide_columns_are_primary_numeric(good_wide, cat, "county").passed

    bad_wide = pl.DataFrame({"fips": ["08001"], "name": ["x"], "d.unknown": [1.0]})
    assert not verify.check_wide_columns_are_primary_numeric(bad_wide, cat, "county").passed


def test_topojson_fips_join_check(tmp_path: Path) -> None:
    topo = {
        "type": "Topology",
        "objects": {
            "counties": {
                "type": "GeometryCollection",
                "geometries": [
                    {"type": "Polygon", "id": "08001", "arcs": []},
                    {"type": "Polygon", "id": "08031", "arcs": []},
                ],
            }
        },
        "arcs": [],
    }
    p = tmp_path / "co_counties.topojson"
    p.write_text(json.dumps(topo))

    matching = pl.DataFrame({"fips": ["08001", "08031"], "name": ["a", "b"]})
    assert verify.check_topojson_fips_joins(p, matching, "county").passed

    missing = pl.DataFrame({"fips": ["08001"], "name": ["a"]})
    assert not verify.check_topojson_fips_joins(p, missing, "county").passed


def _write_minimal_snapshot(tmp_path: Path, orphan: bool = False) -> None:
    cat = _catalog(["percent", "ordinal"] if not orphan else ["percent"],
                   [True, False] if not orphan else [True])
    cat.write_parquet(tmp_path / "catalog.parquet")

    long_id = "d.bogus" if orphan else "d.m0"
    pl.DataFrame(
        {
            "fips": ["08001"],
            "measure_id": [long_id],
            "value": [42.0],
            "value_str": [None],
            "aac": [None],
        },
        schema=_LONG_SCHEMA,
    ).write_parquet(tmp_path / "county_long.parquet")

    pl.DataFrame(
        {
            "fips": [f"08{i:03d}" for i in range(64)],
            "name": ["x"] * 64,
            "d.m0": [1.0] * 64,
        }
    ).write_parquet(tmp_path / "county_wide.parquet")

    pl.DataFrame(schema=_LONG_SCHEMA).write_parquet(tmp_path / "tract_long.parquet")
    pl.DataFrame({"fips": ["08001001000"], "name": ["x"]}).write_parquet(
        tmp_path / "tract_wide.parquet"
    )


def test_run_offline_checks_smoke(tmp_path: Path) -> None:
    _write_minimal_snapshot(tmp_path)
    results = verify.run_offline_checks(tmp_path)
    failed = [r for r in results if not r.passed]
    assert not failed, failed


def test_run_offline_checks_catches_orphan(tmp_path: Path) -> None:
    _write_minimal_snapshot(tmp_path, orphan=True)
    results = verify.run_offline_checks(tmp_path)
    orphan_check = next(r for r in results if "county_long" in r.name)
    assert not orphan_check.passed
