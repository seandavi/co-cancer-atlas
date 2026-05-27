"""Acceptance probe — confirm the snapshot's Denver figures match SCP.

History: in Phase 1.0 this probe resolved SPEC §9 by hitting the live
ECCO API and comparing Denver County's All Cancer Sites incidence to
the published State Cancer Profiles figures (resolution: ``aac`` =
average annual count; ECCO ≈ SCP within 1%).

The Phase 1.6 migration changed the data source: cancer rates and
counts now come *directly* from the SCP scraper release (see
:mod:`.scp`), so the ECCO-vs-SCP comparison is no longer interesting —
they're the same source. This probe now reads the materialised
``county_long.parquet`` snapshot and confirms it still matches the
published SCP figure within tolerance.

The probe stays idempotent and re-runs as part of :mod:`.verify`,
catching drift either from a stale SCP release pin in :mod:`.scp` or
from accidental schema breakage in :func:`.scp.build_scp`.

Run:
    uv run python -m co_cancer_atlas_etl.aac_probe
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

# Anchor: Denver County, all cancer sites incidence, primary series
# (default factors: All Races, Both Sexes → All, All Ages, All Stages).
ANCHOR_FIPS = "08031"
ANCHOR_MEASURE_ID = "scpincidence.All Cancer Sites"

# Reference figures from State Cancer Profiles (release 2026-05-01).
# Update if SCP republishes for a different year range.
#
#   https://statecancerprofiles.cancer.gov/incidencerates/index.php
#     ?stateFIPS=08&areatype=county&cancer=001&race=00&sex=0
#     &age=001&stage=999&year=0&type=incd
SCP_REFERENCE_URL = (
    "https://statecancerprofiles.cancer.gov/incidencerates/index.php"
    "?stateFIPS=08&areatype=county&cancer=001&race=00&sex=0"
    "&age=001&stage=999&year=0&type=incd&output=1"
)
SCP_REFERENCE_RATE = 400.3      # cases per 100,000, age-adjusted
SCP_REFERENCE_COUNT = 2765.0    # average annual count

# Tolerance is tight — we're reading the same source SCP publishes. Any
# >1% delta is a code or pinning bug, not a real epidemiologic shift.
TOLERANCE = 0.01

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def probe(data_dir: Path | None = None) -> int:
    if data_dir is None:
        data_dir = DEFAULT_DATA_DIR

    long_path = data_dir / "county_long.parquet"
    if not long_path.exists():
        print(f"FAIL: snapshot missing at {long_path}", file=sys.stderr)
        return 1

    long_df = pl.read_parquet(long_path)
    row = long_df.filter(
        (pl.col("fips") == ANCHOR_FIPS)
        & (pl.col("measure_id") == ANCHOR_MEASURE_ID)
    )
    if row.height == 0:
        print(
            f"FAIL: no row for {ANCHOR_MEASURE_ID} at FIPS {ANCHOR_FIPS}",
            file=sys.stderr,
        )
        return 1

    value = row.get_column("value")[0]
    aac = row.get_column("aac")[0]
    value_lo = row.get_column("value_lo")[0]
    value_hi = row.get_column("value_hi")[0]

    rate_delta = abs(value - SCP_REFERENCE_RATE) / SCP_REFERENCE_RATE
    count_delta = abs(aac - SCP_REFERENCE_COUNT) / SCP_REFERENCE_COUNT

    print("=" * 60)
    print("aac probe — snapshot vs SCP reference")
    print("=" * 60)
    print(f"anchor          : Denver County, CO (FIPS {ANCHOR_FIPS})")
    print(f"measure_id      : {ANCHOR_MEASURE_ID}")
    print()
    print(f"{'':24} {'snapshot':>10} {'SCP':>10} {'delta':>10}")
    print(
        f"{'value (rate / 100k)':24} {value:>10.1f} "
        f"{SCP_REFERENCE_RATE:>10.1f} {rate_delta:>9.1%}"
    )
    print(
        f"{'aac (annual count)':24} {aac:>10.1f} "
        f"{SCP_REFERENCE_COUNT:>10.1f} {count_delta:>9.1%}"
    )
    print(f"{'95% CI on rate':24} [{value_lo:>4.1f}, {value_hi:>5.1f}]")
    print()
    print(f"SCP reference URL: {SCP_REFERENCE_URL}")
    print()

    if rate_delta > TOLERANCE or count_delta > TOLERANCE:
        print(f"FAIL: delta exceeds tolerance ({TOLERANCE:.0%}).")
        print("      The SCP release pin in scp.DEFAULT_SCP_RELEASE may be")
        print("      stale, or the published figures may have been revised.")
        return 1

    print(f"PASS: both within {TOLERANCE:.0%} of SCP reference.")
    return 0


def main() -> None:
    sys.exit(probe())


if __name__ == "__main__":
    main()
