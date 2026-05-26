"""Build TopoJSON for Colorado counties and tracts via mapshaper.

Geometry comes from `/counties` and `/tracts`. Each row carries the
GeoJSON polygon as a JSON-encoded string in `wkb_geometry` (the field
name is misleading — the API returns GeoJSON, not WKB).

We hand the FeatureCollection to mapshaper (npx) for two reasons:
- the ECCO source has self-intersecting polygons that the pure-Python
  topojson library encodes into arcs Vega-Lite's projection auto-fit
  rejects. mapshaper's `-simplify` step repairs intersections during
  Douglas-Peucker.
- the output is several times smaller for equivalent fidelity.

Object naming: mapshaper names the TopoJSON object after the input
filename. We write `co_counties.geojson` → the object becomes
`co_counties`. The chat tools' `feature` parameter must match.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

# The API column carrying GeoJSON-as-string.
_GEOMETRY_COL = "wkb_geometry"

_COUNTY_FIPS = "us_fips"
_TRACT_FIPS = "fips"
_COUNTY_NAME_FIELDS = ("full", "label", "county")

# Douglas-Peucker simplification ratio. 5% keeps county outlines
# crisp at typical viewport sizes while shrinking the output by ~10x.
_SIMPLIFY = "5%"


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
    features = []
    for row in rows:
        fips = row.get(_COUNTY_FIPS)
        geom_raw = row.get(_GEOMETRY_COL)
        if not fips or not geom_raw:
            continue
        geometry = json.loads(geom_raw) if isinstance(geom_raw, str) else geom_raw
        features.append(
            _feature(str(fips), geometry, {"name": _county_name(row)}),
        )
    return {"type": "FeatureCollection", "features": features}


def tracts_to_feature_collection(rows: list[dict[str, Any]]) -> dict[str, Any]:
    features = []
    for row in rows:
        fips = row.get(_TRACT_FIPS)
        geom_raw = row.get(_GEOMETRY_COL)
        if not fips or not geom_raw:
            continue
        geometry = json.loads(geom_raw) if isinstance(geom_raw, str) else geom_raw
        features.append(_feature(str(fips), geometry, {"name": str(fips)}))
    return {"type": "FeatureCollection", "features": features}


def to_topojson_via_mapshaper(
    feature_collection: dict[str, Any], object_name: str, tmp_dir: Path
) -> str:
    """Convert a FeatureCollection to a TopoJSON string by shelling out
    to `npx mapshaper`. Object name matches the input filename
    (without extension).
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    in_path = tmp_dir / f"{object_name}.geojson"
    out_path = tmp_dir / f"{object_name}.topojson"
    in_path.write_text(json.dumps(feature_collection), encoding="utf-8")

    subprocess.run(
        [
            "npx",
            "--yes",
            "mapshaper@latest",
            str(in_path),
            "-simplify",
            _SIMPLIFY,
            "-o",
            "format=topojson",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    return out_path.read_text(encoding="utf-8")


def write_topojson(
    feature_collection: dict[str, Any], object_name: str, out_path: Path
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    topo = to_topojson_via_mapshaper(
        feature_collection, object_name, out_path.parent / ".cache"
    )
    out_path.write_text(topo, encoding="utf-8")
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
        object_name="co_counties",
        out_path=data_dir / "co_counties.topojson",
    )
    tr_path = write_topojson(
        tracts_to_feature_collection(tracts),
        object_name="co_tracts",
        out_path=data_dir / "co_tracts.topojson",
    )
    return co_path, tr_path


if __name__ == "__main__":
    import asyncio

    co, tr = asyncio.run(main())
    print(f"wrote {co}")
    print(f"wrote {tr}")
