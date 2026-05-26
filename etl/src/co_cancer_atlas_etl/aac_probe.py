"""Phase 1.0 probe — resolve SPEC §9: what does `aac` mean?

The ECCO `fips-value` response carries `value` and `aac` per county for
each cancer measure. SPEC §3 conjectured that `aac` is the annual
average count accompanying the age-adjusted rate. This probe confirms
that against a published State Cancer Profiles figure for Denver
County (the most reliably published Colorado county).

The probe is idempotent: it can be re-run after a snapshot refresh to
detect drift between ECCO and SCP. A small delta (~1%) is expected
because the two sources may publish for slightly different year
ranges.

Run with:
    uv run python -m co_cancer_atlas_etl.aac_probe
"""

from __future__ import annotations

import sys

from .client import EccoClient

# Anchor: Denver County, all cancer sites incidence, all races, both sexes.
ANCHOR_FIPS = "08031"
ANCHOR_DATASET = "scpincidence"
ANCHOR_MEASURE = "All Cancer Sites"

# Reference figures from State Cancer Profiles (2018-2022, all races, both sexes,
# age-adjusted, all ages, all stages). Update if SCP republishes.
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

# Allow ~5% drift — published year ranges can shift between ECCO and SCP.
TOLERANCE = 0.05


def probe() -> int:
    with EccoClient() as ecco:
        resp = ecco.fips_value(ANCHOR_DATASET, ANCHOR_MEASURE)

    row = resp.get("values", {}).get(ANCHOR_FIPS)
    if row is None:
        print(f"FAIL: no row for FIPS {ANCHOR_FIPS} in response", file=sys.stderr)
        return 1

    value = row.get("value")
    aac = row.get("aac")
    unit = resp.get("unit")
    state = resp.get("state")

    rate_delta = abs(value - SCP_REFERENCE_RATE) / SCP_REFERENCE_RATE
    count_delta = abs(aac - SCP_REFERENCE_COUNT) / SCP_REFERENCE_COUNT

    print("=" * 60)
    print("aac probe — SPEC §9 resolution")
    print("=" * 60)
    print(f"anchor          : Denver County, CO (FIPS {ANCHOR_FIPS})")
    print(f"measure         : {ANCHOR_DATASET} / {ANCHOR_MEASURE!r}")
    print(f"unit            : {unit}")
    print(f"state reference : {state}")
    print()
    print(f"{'':24} {'ECCO':>10} {'SCP':>10} {'delta':>10}")
    print(f"{'value (rate / 100k)':24} {value:>10.1f} "
          f"{SCP_REFERENCE_RATE:>10.1f} {rate_delta:>9.1%}")
    print(f"{'aac (annual count)':24} {aac:>10.1f} "
          f"{SCP_REFERENCE_COUNT:>10.1f} {count_delta:>9.1%}")
    print()
    print(f"SCP reference URL: {SCP_REFERENCE_URL}")
    print()

    if rate_delta > TOLERANCE or count_delta > TOLERANCE:
        print(f"FAIL: delta exceeds tolerance ({TOLERANCE:.0%}). The aac")
        print("      interpretation may have changed, or SCP republished")
        print("      for a different year range. Investigate before")
        print("      relying on aac in the UI.")
        return 1

    print(f"PASS: both within {TOLERANCE:.0%} of SCP reference.")
    print("Interpretation: `value` = age-adjusted rate per 100,000;")
    print("                `aac`   = average annual count.")
    return 0


def main() -> None:
    sys.exit(probe())


if __name__ == "__main__":
    main()
