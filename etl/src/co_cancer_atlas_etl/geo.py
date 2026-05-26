"""Build TopoJSON for Colorado counties and tracts.

Geometry comes from `/counties` and `/tracts`. Each row carries the
GeoJSON polygon as a JSON-encoded string in `wkb_geometry` (despite
the misleading name — the API returns GeoJSON, not WKB).

We assemble a GeoJSON FeatureCollection with `feature.id` set to the
FIPS string that joins to `county_wide.fips` / `tract_wide.fips`,
then convert to TopoJSON (Vega-Lite consumes TopoJSON natively and
the result is several times smaller than the GeoJSON).

Acceptance (per SPEC §7 Phase 1):
- Every feature has `id` = a valid FIPS that joins 1:1 to `*_wide.fips`.
- Output is a single TopoJSON object per level.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import topojson

# The API column carrying GeoJSON-as-string.
_GEOMETRY_COL = "wkb_geometry"

# Per SPEC: the catalog/wide tables use the 5-digit county FIPS
# (state + county). The /counties response calls that `us_fips`;
# /tracts returns its 11-digit FIPS as `fips`.
_COUNTY_FIPS = "us_fips"
_TRACT_FIPS = "fips"

# Field names for the human-readable label we attach to each feature.
_COUNTY_NAME_FIELDS = ("full", "label", "county")


def _county_name(row: dict[str, Any]) -> str:
    for k in _COUNTY_NAME_FIELDS:
        v = row.get(k)
        if v:
            return str(v)
    return str(row.get(_COUNTY_FIPS, ""))


def _feature(
    fips: str, geometry: dict[str, Any], properties: dict[str, Any]
) -> dict[str, Any]:
    return {
        "type": "Feature",
        "id": fips,
        "properties": {**properties, "fips": fips},
        "geometry": geometry,
    }


def counties_to_feature_collection(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble a FeatureCollection from /counties rows."""
    features = []
    for row in rows:
        fips = row.get(_COUNTY_FIPS)
        geom_raw = row.get(_GEOMETRY_COL)
        if not fips or not geom_raw:
            continue
        geometry = json.loads(geom_raw) if isinstance(geom_raw, str) else geom_raw
        features.append(
            _feature(
                str(fips),
                geometry,
                {"name": _county_name(row)},
            )
        )
    return {"type": "FeatureCollection", "features": features}


def tracts_to_feature_collection(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble a FeatureCollection from /tracts rows."""
    features = []
    for row in rows:
        fips = row.get(_TRACT_FIPS)
        geom_raw = row.get(_GEOMETRY_COL)
        if not fips or not geom_raw:
            continue
        geometry = json.loads(geom_raw) if isinstance(geom_raw, str) else geom_raw
        features.append(
            _feature(str(fips), geometry, {"name": str(fips)})
        )
    return {"type": "FeatureCollection", "features": features}


def to_topojson(feature_collection: dict[str, Any], object_name: str) -> str:
    """GeoJSON → TopoJSON JSON string.

    `object_name` is the key under `topology.objects` (e.g.
    "counties"); Vega-Lite specs reference this name.
    """
    topo = topojson.Topology(
        feature_collection,
        prequantize=True,
        object_name=object_name,
    )
    return topo.to_json()


def write_topojson(feature_collection: dict[str, Any], object_name: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(to_topojson(feature_collection, object_name), encoding="utf-8")
    return out_path


async def main(data_dir: Path | None = None) -> tuple[Path, Path]:
    """Fetch county + tract geometry, write both TopoJSON files."""
    from .fetch import AsyncEccoClient

    if data_dir is None:
        data_dir = Path(__file__).resolve().parents[3] / "data"

    async with AsyncEccoClient() as ecco:
        counties = await ecco.counties()
        tracts = await ecco.tracts()

    co_path = write_topojson(
        counties_to_feature_collection(counties),
        object_name="counties",
        out_path=data_dir / "co_counties.topojson",
    )
    tr_path = write_topojson(
        tracts_to_feature_collection(tracts),
        object_name="tracts",
        out_path=data_dir / "co_tracts.topojson",
    )
    return co_path, tr_path


if __name__ == "__main__":
    import asyncio

    co, tr = asyncio.run(main())
    print(f"wrote {co}")
    print(f"wrote {tr}")
