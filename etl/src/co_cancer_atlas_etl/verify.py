"""Phase 1.5 — acceptance checks for the data/ snapshot.

Verifies every condition listed in SPEC §7 Phase 1 plus the standing
`aac` probe. Prints a checklist, exits 0 on full pass and 1 on any
failure. Designed to be wired into both the local `verify` script and
the GitHub Actions etl-refresh workflow's pre-PR gate.

Run:
    uv run python -m co_cancer_atlas_etl.verify
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from .catalog import NUMERIC_UNITS

# The enum from SPEC §3. unit must be one of these.
_VALID_UNITS: frozenset[str] = frozenset(
    {
        "percent",
        "rate",
        "count",
        "dollar_amount",
        "rank",
        "ordinal",
        "categorical",
        "least_most",
    }
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str

    def render(self) -> str:
        marker = "PASS" if self.passed else "FAIL"
        return f"  [{marker}] {self.name}\n         {self.detail}"


# ----- individual checks -------------------------------------------------


def check_catalog_units(catalog: pl.DataFrame) -> CheckResult:
    bad = catalog.filter(~pl.col("unit").is_in(_VALID_UNITS))
    if bad.height:
        sample = bad.select(["measure_id", "unit"]).head(3).to_dicts()
        return CheckResult(
            "catalog.unit ⊆ MeasureUnit enum",
            False,
            f"{bad.height} catalog rows carry an unrecognized unit; e.g. {sample}",
        )
    return CheckResult(
        "catalog.unit ⊆ MeasureUnit enum",
        True,
        f"all {catalog.height} catalog rows use a valid unit",
    )


def check_is_numeric_matches_unit(catalog: pl.DataFrame) -> CheckResult:
    expected = catalog.with_columns(
        expected=pl.col("unit").is_in(NUMERIC_UNITS)
    )
    mismatches = expected.filter(pl.col("is_numeric") != pl.col("expected"))
    if mismatches.height:
        return CheckResult(
            "is_numeric flag matches unit",
            False,
            f"{mismatches.height} rows where is_numeric disagrees with unit",
        )
    return CheckResult(
        "is_numeric flag matches unit",
        True,
        f"{catalog.height} rows agree (numeric units → True, else False)",
    )


def check_county_wide_64_rows(county_wide: pl.DataFrame) -> CheckResult:
    if county_wide.height != 64:
        return CheckResult(
            "county_wide has 64 rows",
            False,
            f"got {county_wide.height}",
        )
    return CheckResult("county_wide has 64 rows", True, "64 ✓")


def check_fips_leading_zeros_preserved(df: pl.DataFrame, label: str) -> CheckResult:
    fips_col = df.get_column("fips")
    if fips_col.dtype != pl.Utf8:
        return CheckResult(
            f"{label}.fips is string",
            False,
            f"dtype is {fips_col.dtype}, not Utf8",
        )
    co_only = [f for f in fips_col.to_list() if f.startswith("08")]
    if not co_only:
        return CheckResult(
            f"{label}.fips leading zeros preserved",
            False,
            "no FIPS starts with '08' — leading zero may have been dropped",
        )
    return CheckResult(
        f"{label}.fips leading zeros preserved",
        True,
        f"{len(co_only)} FIPS values start with '08' (e.g. {co_only[:3]})",
    )


def check_long_measure_ids_in_catalog(
    long: pl.DataFrame, catalog: pl.DataFrame, label: str
) -> CheckResult:
    catalog_ids = set(catalog.get_column("measure_id").to_list())
    long_primary_ids = {mid.split("#", 1)[0] for mid in long.get_column("measure_id").to_list()}
    orphans = long_primary_ids - catalog_ids
    if orphans:
        return CheckResult(
            f"{label}.measure_id ⊆ catalog (primary id)",
            False,
            f"{len(orphans)} orphan primary ids; e.g. {sorted(orphans)[:3]}",
        )
    return CheckResult(
        f"{label}.measure_id ⊆ catalog (primary id)",
        True,
        f"{len(long_primary_ids)} distinct primary ids, all in catalog",
    )


def check_wide_columns_are_primary_numeric(
    wide: pl.DataFrame, catalog: pl.DataFrame, level: str
) -> CheckResult:
    primary_numeric = set(
        catalog.filter(
            (pl.col("level") == level) & pl.col("is_numeric")
        ).get_column("measure_id").to_list()
    )
    wide_cols = set(wide.columns) - {"fips", "name"}
    extras = wide_cols - primary_numeric
    if extras:
        return CheckResult(
            f"{level}_wide columns ⊆ primary-numeric catalog ids",
            False,
            f"{len(extras)} columns aren't primary-numeric ids; e.g. {sorted(extras)[:3]}",
        )
    return CheckResult(
        f"{level}_wide columns ⊆ primary-numeric catalog ids",
        True,
        f"{len(wide_cols)} columns, all primary-numeric",
    )


def check_scp_national_row_present(
    long: pl.DataFrame, catalog: pl.DataFrame
) -> CheckResult:
    """Every primary SCP cancer measure should have a FIPS 00000 row.

    The chat tool anchors comparisons to the US national row; if it's
    missing for a measure, the prompt's "compare to national" pattern
    silently degrades.
    """
    primary_scp = set(
        catalog.filter(
            pl.col("dataset").is_in(["scpincidence", "scpdeaths"])
        )
        .get_column("measure_id")
        .to_list()
    )
    national_ids = set(
        long.filter(pl.col("fips") == "00000")
        .get_column("measure_id")
        .to_list()
    )
    missing = primary_scp - {mid.split("#", 1)[0] for mid in national_ids}
    if missing:
        return CheckResult(
            "every primary SCP measure has a national row",
            False,
            f"{len(missing)} measures missing FIPS 00000; "
            f"e.g. {sorted(missing)[:3]}",
        )
    return CheckResult(
        "every primary SCP measure has a national row",
        True,
        f"{len(primary_scp)} primary SCP measures all anchor to US",
    )


def check_scp_ci_ordering(long: pl.DataFrame) -> CheckResult:
    """value_lo ≤ value ≤ value_hi where all three are populated.

    Empty input passes — this check is meaningful only when SCP rows are
    actually present. The companion ``check_scp_national_row_present``
    catches the schema-broken case (catalog has SCP measures, long table
    has none).
    """
    if "value_lo" not in long.columns:
        return CheckResult(
            "SCP rate CI ordering (lo ≤ value ≤ hi)",
            False,
            "long table missing value_lo column — schema is broken",
        )
    scp = long.filter(pl.col("value_lo").is_not_null())
    if scp.height == 0:
        return CheckResult(
            "SCP rate CI ordering (lo ≤ value ≤ hi)",
            True,
            "no CI-bearing rows present (empty SCP slice — vacuously ok)",
        )
    bad = scp.filter(
        (pl.col("value_lo") > pl.col("value"))
        | (pl.col("value") > pl.col("value_hi"))
    )
    if bad.height:
        return CheckResult(
            "SCP rate CI ordering (lo ≤ value ≤ hi)",
            False,
            f"{bad.height} rows violate CI ordering",
        )
    return CheckResult(
        "SCP rate CI ordering (lo ≤ value ≤ hi)",
        True,
        f"{scp.height} CI-bearing rows are well-ordered",
    )


def check_scp_trend_strings(long: pl.DataFrame) -> CheckResult:
    """trend_str ∈ {stable, rising, falling, null}."""
    valid = {"stable", "rising", "falling", None}
    distinct = set(long.get_column("trend_str").unique().to_list())
    unknown = distinct - valid
    if unknown:
        return CheckResult(
            "trend_str ⊆ {stable, rising, falling, null}",
            False,
            f"unexpected trend strings: {sorted(str(v) for v in unknown)}",
        )
    return CheckResult(
        "trend_str ⊆ {stable, rising, falling, null}",
        True,
        f"distinct values: {sorted(str(v) for v in distinct)}",
    )


def check_topojson_fips_joins(
    topo_path: Path, wide: pl.DataFrame, level: str
) -> CheckResult:
    topo = json.loads(topo_path.read_text(encoding="utf-8"))
    objects = topo.get("objects") or {}
    # The single object holds the geometry list — use whichever name is there.
    if not objects:
        return CheckResult(
            f"{level} TopoJSON joins to {level}_wide",
            False,
            f"{topo_path.name} has no `objects`",
        )
    geometries = next(iter(objects.values())).get("geometries", [])
    topo_ids = {str(g.get("id")) for g in geometries if g.get("id") is not None}
    wide_ids = set(wide.get_column("fips").to_list())

    only_in_topo = topo_ids - wide_ids
    only_in_wide = wide_ids - topo_ids
    if only_in_topo or only_in_wide:
        return CheckResult(
            f"{level} TopoJSON joins to {level}_wide",
            False,
            f"{len(only_in_topo)} ids in topo not in wide; "
            f"{len(only_in_wide)} ids in wide not in topo",
        )
    return CheckResult(
        f"{level} TopoJSON joins to {level}_wide",
        True,
        f"{len(topo_ids)} ids match 1:1 between topo and wide",
    )


# ----- aac probe (re-runs the Phase 1.0 spot-check) ----------------------


def check_aac_probe() -> CheckResult:
    from . import aac_probe

    code = aac_probe.probe()
    return CheckResult(
        "aac probe: ECCO matches SCP within tolerance",
        code == 0,
        "see preceding probe output",
    )


# ----- runner -----------------------------------------------------------


def _open(data_dir: Path, name: str) -> pl.DataFrame:
    return pl.read_parquet(data_dir / name)


def run_offline_checks(data_dir: Path) -> list[CheckResult]:
    """Every check that can run without touching the network."""
    catalog = _open(data_dir, "catalog.parquet")
    county_long = _open(data_dir, "county_long.parquet")
    county_wide = _open(data_dir, "county_wide.parquet")
    tract_long = _open(data_dir, "tract_long.parquet")
    tract_wide = _open(data_dir, "tract_wide.parquet")

    checks: list[CheckResult] = [
        check_catalog_units(catalog),
        check_is_numeric_matches_unit(catalog),
        check_county_wide_64_rows(county_wide),
        check_fips_leading_zeros_preserved(county_wide, "county_wide"),
        check_fips_leading_zeros_preserved(tract_wide, "tract_wide"),
        check_long_measure_ids_in_catalog(county_long, catalog, "county_long"),
        check_long_measure_ids_in_catalog(tract_long, catalog, "tract_long"),
        check_wide_columns_are_primary_numeric(county_wide, catalog, "county"),
        check_wide_columns_are_primary_numeric(tract_wide, catalog, "tract"),
        check_scp_national_row_present(county_long, catalog),
        check_scp_ci_ordering(county_long),
        check_scp_trend_strings(county_long),
    ]

    co_topo = data_dir / "co_counties.topojson"
    tr_topo = data_dir / "co_tracts.topojson"
    if co_topo.exists():
        checks.append(check_topojson_fips_joins(co_topo, county_wide, "county"))
    if tr_topo.exists():
        checks.append(check_topojson_fips_joins(tr_topo, tract_wide, "tract"))
    return checks


def verify(data_dir: Path | None = None, *, include_aac_probe: bool = True) -> int:
    if data_dir is None:
        data_dir = Path(__file__).resolve().parents[3] / "data"

    print(f"verifying {data_dir}")
    checks = run_offline_checks(data_dir)
    if include_aac_probe:
        checks.append(check_aac_probe())

    for c in checks:
        print(c.render())

    failed = sum(1 for c in checks if not c.passed)
    print()
    print(f"{len(checks) - failed} / {len(checks)} checks passed")
    return 0 if failed == 0 else 1


def main() -> None:
    sys.exit(verify())


if __name__ == "__main__":
    main()
