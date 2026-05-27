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
from .scp import (
    DEFAULT_SCP_RELEASE,
    ECCO_CANCER_DATASETS,
    LONG_SCHEMA,
    build_scp,
)

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
    scp_release: str = DEFAULT_SCP_RELEASE,
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)

    overall_start = time.time()

    # 1. Catalog. Every later step reads this; pull it first.
    #
    # Cancer datasets are sourced from the SCP scraper release (see
    # scp.py), not ECCO. Drop ECCO's cancer rows up front so the pivot
    # pass doesn't fan out tens of thousands of fips-value calls we
    # don't need, and so the combined catalog has a single owner per
    # measure_id.
    async with AsyncEccoClient(concurrency=concurrency) as ecco:
        raw_catalog = await ecco.catalog()
    ecco_catalog = build_catalog(raw_catalog).filter(
        ~pl.col("dataset").is_in(list(ECCO_CANCER_DATASETS))
    )
    print(
        f"[snapshot] catalog (ECCO, post-filter): {ecco_catalog.height} "
        f"measures (dropped ECCO scp* datasets — sourced from SCP release)"
    )

    print(f"[snapshot] SCP release: {scp_release}")
    scp_catalog, scp_long = await asyncio.to_thread(build_scp, scp_release)
    print(
        f"[snapshot] catalog (SCP): {scp_catalog.height} measures, "
        f"{scp_long.height} long rows"
    )

    catalog_df = pl.concat(
        [ecco_catalog, scp_catalog], how="vertical_relaxed"
    ).sort(["level", "dataset", "measure"])
    write_catalog(catalog_df, data_dir / "catalog.parquet")
    print(f"[snapshot] catalog (combined): {catalog_df.height} measures")

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
        #
        # SCP long rows are county-level only. ECCO's pivot pass still
        # produces all the non-cancer county + tract long rows. We
        # concatenate SCP into the county frame after the ECCO pull so
        # both sources land in one ``county_long.parquet``.
        states_by_level: dict[str, dict[str, float]] = {}
        for level in ("county", "tract"):
            t0 = time.time()
            long_df, states = await build_long_for_level(
                ecco, catalog_df, level, progress=True
            )
            if level == "county":
                long_df = pl.concat(
                    [long_df, scp_long.select(*LONG_SCHEMA.keys())],
                    how="vertical_relaxed",
                ).sort(["measure_id", "fips"])
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
    parser.add_argument(
        "--scp-release",
        type=str,
        default=DEFAULT_SCP_RELEASE,
        help=(
            "SCP scraper release tag (default: %(default)s). Format "
            "YYYY-MM-DD, matching the GitHub release tag at "
            "https://github.com/seandavi/state-cancer-profile-scraper/releases"
        ),
    )
    args = parser.parse_args()
    asyncio.run(
        snapshot(
            data_dir=args.data_dir,
            concurrency=args.concurrency,
            scp_release=args.scp_release,
        )
    )


if __name__ == "__main__":
    main()
