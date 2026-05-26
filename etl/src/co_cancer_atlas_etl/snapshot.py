"""Phase 1.6 — orchestrator. Build the full data/ snapshot.

Produces (under `data/`):
  - catalog.parquet
  - county_long.parquet, county_wide.parquet
  - tract_long.parquet, tract_wide.parquet
  - co_counties.topojson, co_tracts.topojson

Run:
    uv run python -m co_cancer_atlas_etl.snapshot
    uv run python -m co_cancer_atlas_etl.snapshot --data-dir path/to/data

A second back-to-back run against the same API state yields byte-
stable parquet (SPEC §8 — sort rows deterministically before write,
catalog enumeration is sorted, zstd is deterministic).
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

import polars as pl

from .catalog import build_catalog, write_catalog
from .fetch import AsyncEccoClient
from .geo import (
    counties_to_feature_collection,
    tracts_to_feature_collection,
    write_topojson,
)
from .pivot import apply_state_values, build_long_for_level, build_wide

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def _region_names_from_counties(counties: list[dict]) -> pl.DataFrame:
    return (
        pl.DataFrame(
            {
                "fips": [c["us_fips"] for c in counties],
                "name": [c.get("full") or c["us_fips"] for c in counties],
            }
        )
        .unique(subset=["fips"])
        .sort("fips")
    )


def _region_names_from_tracts(tracts: list[dict]) -> pl.DataFrame:
    return (
        pl.DataFrame(
            {
                "fips": [t["fips"] for t in tracts],
                "name": [t["fips"] for t in tracts],
            }
        )
        .unique(subset=["fips"])
        .sort("fips")
    )


async def snapshot(
    data_dir: Path = DEFAULT_DATA_DIR,
    concurrency: int = 8,
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)

    overall_start = time.time()

    # 1. Catalog. Every later step reads this; pull it first.
    async with AsyncEccoClient(concurrency=concurrency) as ecco:
        raw_catalog = await ecco.catalog()
    catalog_df = build_catalog(raw_catalog)
    write_catalog(catalog_df, data_dir / "catalog.parquet")
    print(f"[snapshot] catalog: {catalog_df.height} measures")

    # 2. Geometry + values, fanned out.
    async with AsyncEccoClient(concurrency=concurrency) as ecco:
        counties_task = asyncio.create_task(ecco.counties())
        tracts_task = asyncio.create_task(ecco.tracts())
        counties, tracts = await asyncio.gather(counties_task, tracts_task)

        write_topojson(
            counties_to_feature_collection(counties),
            object_name="counties",
            out_path=data_dir / "co_counties.topojson",
        )
        write_topojson(
            tracts_to_feature_collection(tracts),
            object_name="tracts",
            out_path=data_dir / "co_tracts.topojson",
        )
        print(
            f"[snapshot] topojson: {len(counties)} counties, "
            f"{len(tracts)} tracts"
        )

        region_names = {
            "county": _region_names_from_counties(counties),
            "tract": _region_names_from_tracts(tracts),
        }

        # 3. Long + wide, per level.
        states_by_level: dict[str, dict[str, float]] = {}
        for level in ("county", "tract"):
            t0 = time.time()
            long_df, states = await build_long_for_level(
                ecco, catalog_df, level, progress=True
            )
            wide_df = build_wide(long_df, catalog_df, level, region_names[level])
            long_df.write_parquet(
                data_dir / f"{level}_long.parquet",
                compression="zstd",
                statistics=True,
            )
            wide_df.write_parquet(
                data_dir / f"{level}_wide.parquet",
                compression="zstd",
                statistics=True,
            )
            states_by_level[level] = states
            print(
                f"[snapshot] {level}: long={long_df.height} "
                f"wide={wide_df.height}x{wide_df.width} "
                f"({time.time() - t0:.1f}s)"
            )

    # 4. Backfill catalog.state_value from what pivot collected.
    catalog_with_state = apply_state_values(catalog_df, states_by_level)
    write_catalog(catalog_with_state, data_dir / "catalog.parquet")
    populated = catalog_with_state.filter(
        pl.col("state_value").is_not_null()
    ).height
    print(
        f"[snapshot] catalog state_value: {populated}/{catalog_with_state.height} "
        f"measures backfilled"
    )

    print(f"[snapshot] done in {time.time() - overall_start:.1f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="output directory (default: repo's data/)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="API concurrency cap (default: 8)",
    )
    args = parser.parse_args()
    asyncio.run(snapshot(data_dir=args.data_dir, concurrency=args.concurrency))


if __name__ == "__main__":
    main()
