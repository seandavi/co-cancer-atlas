"""Adapter for the State Cancer Profiles (SCP) scraper release dataset.

Replaces the four ECCO cancer datasets (``scpincidence``, ``scpdeaths``,
``scpincidencetrend``, ``scpdeathstrend``) with rows pulled directly from
the SCP scraper's monthly release on GitHub:

  https://github.com/seandavi/state-cancer-profile-scraper/releases

Why move off ECCO for cancer data
---------------------------------
Per-row, the SCP release carries strict supersets of what ECCO exposed:

- 95% CI on the rate (``lower_ci_rate`` / ``upper_ci_rate``)
- Trend direction *and* slope, inline on the same row, with a CI on the
  slope (``recent_trend``, ``recent_5_year_trend_in_rate``, ``…_lo/_hi``).
  The two trend datasets collapse into columns on the rate row.
- Rural / urban classifier (``2023_rural_urban_continuum_codes…``)
- A national row (FIPS ``00000``) per cancer x factor combo — gives the
  chat tool a real "compare to US" anchor instead of inferring one.

What this module produces
-------------------------
A pair of frames matching the SPEC §4 contract:

- catalog rows for two datasets (``scpincidence``, ``scpdeaths``), one row
  per primary measure, with a factors JSON derived from observed values.
- long-table rows with the standard columns (``fips``, ``measure_id``,
  ``value``, ``value_str``, ``aac``) **plus** the cancer-specific
  extensions defined in :mod:`.pivot`: ``value_lo``, ``value_hi``,
  ``trend_str``, ``trend_pct``, ``trend_pct_lo``, ``trend_pct_hi``,
  ``rural_urban``, ``pct_late_stage``.

The release covers ``areatype=county`` only as of the 2026-05-01 cut, so:

- *County* rows for Colorado come from the release CSV (``state_fips='08'``).
- *National* rows come from the release CSV (``state_fips='00'`` / FIPS
  ``00000``) — SCP appends these to every county query.
- *Colorado state-level rollup* (FIPS ``08000``) is not present yet.
  Pending https://github.com/seandavi/state-cancer-profile-scraper/pull/10
  which iterates over areatype. Until that release lands, ``catalog
  .state_value`` stays null for SCP measures and the chat tool should
  anchor to the national row instead.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import duckdb
import polars as pl

from .catalog import NUMERIC_UNITS, factor_suffix, primary_measure_id

# ---------------------------------------------------------------------------
# Release source
# ---------------------------------------------------------------------------

#: The SCP scraper publishes a monthly release with tag ``YYYY-MM-DD``.
#: Pinned here so the snapshot is reproducible. Bump alongside snapshot
#: refreshes — verify the chosen release is the latest before committing
#: a snapshot run.
DEFAULT_SCP_RELEASE = "2026-05-01"

_RELEASE_BASE = (
    "https://github.com/seandavi/state-cancer-profile-scraper/releases/download"
)


def release_csv_url(release: str, kind: str) -> str:
    """Resolve a release CSV URL by tag + kind.

    kind ∈ {"incidence", "mortality"}. Returns the canonical gzipped CSV
    URL DuckDB's httpfs can read in place.
    """
    if kind not in {"incidence", "mortality"}:
        raise ValueError(f"kind must be 'incidence' or 'mortality', got {kind!r}")
    return f"{_RELEASE_BASE}/{release}/state_cancer_profiles_{kind}.csv.gz"


# ---------------------------------------------------------------------------
# Factor mapping
# ---------------------------------------------------------------------------

# SCP labels every "all" axis with its own phrasing. Map to the ECCO
# catalog's canonical factor values so suffix construction matches what
# the chat tool / system prompt already document.
_SEX_MAP: dict[str, str] = {
    "Both Sexes": "All",
    "Male": "Male",
    "Female": "Female",
}

# Per-axis defaults — these mirror what the ECCO catalog used. Anything
# matching the default is dropped from the measure_id suffix.
_FACTOR_DEFAULTS: dict[str, str] = {
    "age": "All Ages",
    "race": "All Races (includes Hispanic)",
    "sex": "All",
    "stage": "All Stages",
}

# Canonical dataset metadata. Keep dataset_label / source / source_url in
# sync with what the ECCO catalog used so existing chat references hold.
_DATASETS: dict[str, dict[str, str]] = {
    "scpincidence": {
        "dataset_label": "Cancer Incidence (age-adj per 100k)",
        "source": "State Cancer Profiles (NCI)",
        "source_url": "https://statecancerprofiles.cancer.gov/",
    },
    "scpdeaths": {
        "dataset_label": "Cancer Mortality (age-adj per 100k)",
        "source": "State Cancer Profiles (NCI)",
        "source_url": "https://statecancerprofiles.cancer.gov/",
    },
}


def normalize_sex(scp_sex: str) -> str:
    return _SEX_MAP.get(scp_sex, scp_sex)


def _normalize_factors(df: pl.DataFrame) -> pl.DataFrame:
    """Apply factor-value normalization shared across all SCP consumers."""
    return df.with_columns(
        pl.col("sex").map_elements(normalize_sex, return_dtype=pl.Utf8).alias("sex"),
    )


# ---------------------------------------------------------------------------
# CSV → polars
# ---------------------------------------------------------------------------

# Columns we pull from the release CSV. Everything else (year label,
# extracted_at, url, locale_type, state) is either constant for our
# slice or recomputable downstream.
_KEEP_COLUMNS: tuple[str, ...] = (
    "fips",
    "reported_locale",
    "2023_rural_urban_continuum_codesrural_urban_note",
    "age_adjusted_rate_per_100_000",
    "lower_ci_rate",
    "upper_ci_rate",
    "average_annual_count",
    "recent_trend",
    "recent_5_year_trend_in_rate",
    "lower_ci_trend_in_rate",
    "upper_ci_trend_in_rate",
    "sex",
    "stage",
    "race",
    "cancer",
    "age",
    "state_fips",
    "percent_of_cases_with_late_stage",
)


_NUMERIC_RAW_COLUMNS: tuple[str, ...] = (
    "age_adjusted_rate_per_100_000",
    "lower_ci_rate",
    "upper_ci_rate",
    "average_annual_count",
    "recent_5_year_trend_in_rate",
    "lower_ci_trend_in_rate",
    "upper_ci_trend_in_rate",
    "percent_of_cases_with_late_stage",
)


def _read_release_csv(url: str) -> pl.DataFrame:
    """Read a SCP release CSV via DuckDB httpfs, filtered to CO + national.

    DuckDB reads gzipped CSVs over HTTPS in place — no local download.
    The filter happens server-side in DuckDB so we only materialise the
    ~30k rows we actually need (vs the ~1.3M total).

    Reads every column as VARCHAR and converts numerics with TRY_CAST in
    the same query — SCP CSVs mix empty strings, suppressed-value
    markers, and "(footnote)" annotations that defeat dtype inference.
    The mortality CSV is missing ``percent_of_cases_with_late_stage``
    entirely, so we project it as NULL when absent.
    """
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")

    # Inspect the actual columns once so the SELECT can skip ones the
    # mortality file doesn't have (e.g. percent_of_cases_with_late_stage).
    available = {
        row[0]
        for row in con.execute(
            f"DESCRIBE SELECT * FROM read_csv_auto('{url}', all_varchar=true) LIMIT 0"
        ).fetchall()
    }

    select_parts: list[str] = []
    for col in _KEEP_COLUMNS:
        if col not in available:
            select_parts.append(f'NULL AS "{col}"')
        elif col in _NUMERIC_RAW_COLUMNS:
            select_parts.append(
                f"TRY_CAST(NULLIF(NULLIF(\"{col}\", ''), '*') AS DOUBLE) AS \"{col}\""
            )
        else:
            select_parts.append(f'"{col}"')

    sql = f"""
        SELECT {", ".join(select_parts)}
        FROM read_csv_auto('{url}', all_varchar=true)
        WHERE state_fips IN ('08', '00')
          AND TRY_CAST(
              NULLIF(NULLIF(age_adjusted_rate_per_100_000, ''), '*') AS DOUBLE
          ) IS NOT NULL
    """
    arrow_table = con.execute(sql).arrow()
    return pl.from_arrow(arrow_table)


# ---------------------------------------------------------------------------
# Building catalog + long rows
# ---------------------------------------------------------------------------


def _non_default(
    factor_values: dict[str, str], defaults: dict[str, str]
) -> dict[str, str]:
    """Project a factor combination down to only non-default values."""
    return {k: v for k, v in factor_values.items() if v != defaults.get(k)}


def _build_long_rows(
    df: pl.DataFrame,
    dataset: str,
    defaults_by_cancer: dict[str, dict[str, str]],
) -> pl.DataFrame:
    """Materialise long-table rows + measure_id for every SCP row.

    Adds the cancer-specific nullable extensions (CI, trend, rural/urban,
    late-stage). One row per (fips, cancer, factor combo) — same grain
    as the source CSV after we filter to CO + national.

    ``defaults_by_cancer`` maps cancer label → axis → per-cancer
    default value. Sex-specific cancers (Cervix, Prostate, Breast)
    derive their own per-cancer ``sex`` default since the global "All"
    doesn't appear in their rows; this keeps the primary measure_id
    suffix-free.

    Expects sex to already be normalised — callers must run the input
    through :func:`_normalize_factors` first so factor_universe and
    long_rows agree on what "All" means.
    """
    measure_ids = []
    for row in df.iter_rows(named=True):
        combo = {
            "age": row["age"],
            "race": row["race"],
            "sex": row["sex"],
            "stage": row["stage"],
        }
        cancer_defaults = defaults_by_cancer[row["cancer"]]
        suffix = factor_suffix(_non_default(combo, cancer_defaults))
        measure_ids.append(f"{primary_measure_id(dataset, row['cancer'])}{suffix}")

    return df.with_columns(pl.Series("measure_id", measure_ids)).select(
        pl.col("fips"),
        pl.col("measure_id"),
        pl.col("age_adjusted_rate_per_100_000").alias("value"),
        pl.lit(None, dtype=pl.Utf8).alias("value_str"),
        pl.col("average_annual_count").cast(pl.Float64).alias("aac"),
        pl.col("lower_ci_rate").cast(pl.Float64).alias("value_lo"),
        pl.col("upper_ci_rate").cast(pl.Float64).alias("value_hi"),
        pl.col("recent_trend").alias("trend_str"),
        pl.col("recent_5_year_trend_in_rate").cast(pl.Float64).alias("trend_pct"),
        pl.col("lower_ci_trend_in_rate").cast(pl.Float64).alias("trend_pct_lo"),
        pl.col("upper_ci_trend_in_rate").cast(pl.Float64).alias("trend_pct_hi"),
        pl.col("2023_rural_urban_continuum_codesrural_urban_note").alias("rural_urban"),
        pl.col("percent_of_cases_with_late_stage")
        .cast(pl.Float64, strict=False)
        .alias("pct_late_stage"),
    )


def _factor_universe(df: pl.DataFrame) -> dict[str, dict[str, dict[str, Any]]]:
    """Build a per-cancer factors JSON object from observed factor values.

    For each cancer (= measure), record every value seen across each
    axis. The catalog format expects ``{factor: {label, default, values:
    {code: label}}}`` (see SPEC §4 + ECCO catalog).

    The default per axis is the global default *if it appears in the
    observed values for this cancer*, otherwise the alphabetically
    first observed value. This handles sex-specific cancers (Cervix /
    Prostate / Breast) where the global default ``sex='All'`` isn't
    among the published rows.
    """
    axes = ("age", "race", "sex", "stage")
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for cancer, group in df.group_by("cancer", maintain_order=True):
        # cancer comes back as a tuple from group_by
        cancer_label = cancer[0] if isinstance(cancer, tuple) else cancer
        factors: dict[str, dict[str, Any]] = {}
        for axis in axes:
            values = sorted(
                v for v in group.get_column(axis).unique().to_list() if v is not None
            )
            global_default = _FACTOR_DEFAULTS[axis]
            default = global_default if global_default in values else values[0]
            factors[axis] = {
                "default": default,
                "label": axis.capitalize(),
                "values": {v: v for v in values},
            }
        out[cancer_label] = factors
    return out


def _build_catalog_rows(
    df: pl.DataFrame,
    dataset: str,
    factor_universe: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Build catalog rows for every cancer observed in the SCP frame."""
    meta = _DATASETS[dataset]
    # df is unused after factor_universe is computed by the caller, but
    # keep the parameter for symmetry with _build_long_rows.
    _ = df

    rows: list[dict[str, Any]] = []
    for cancer, factors in factor_universe.items():
        rows.append(
            {
                "measure_id": primary_measure_id(dataset, cancer),
                "dataset": dataset,
                "dataset_label": meta["dataset_label"],
                "level": "county",
                "category": meta["dataset_label"],
                "measure": cancer,
                "label": cancer,
                "unit": "rate",
                "source": meta["source"],
                "source_url": meta["source_url"],
                "state_value": None,
                "factors": json.dumps(factors, sort_keys=True, ensure_ascii=False),
                "is_numeric": "rate" in NUMERIC_UNITS,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def build_scp(
    release: str = DEFAULT_SCP_RELEASE,
    *,
    kinds: Iterable[str] = ("incidence", "mortality"),
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Build (catalog_rows, long_rows) from the SCP release CSVs.

    Returns two frames matching the SPEC §4 schemas. The caller is
    responsible for merging these into the broader snapshot pipeline —
    see :func:`co_cancer_atlas_etl.snapshot.snapshot`.
    """
    kind_to_dataset = {"incidence": "scpincidence", "mortality": "scpdeaths"}

    catalog_rows: list[dict[str, Any]] = []
    long_frames: list[pl.DataFrame] = []

    for kind in kinds:
        dataset = kind_to_dataset[kind]
        url = release_csv_url(release, kind)
        raw = _read_release_csv(url)
        if raw.is_empty():
            continue
        normalized = _normalize_factors(raw)
        factor_universe = _factor_universe(normalized)
        defaults_by_cancer = {
            cancer: {axis: factors[axis]["default"] for axis in factors}
            for cancer, factors in factor_universe.items()
        }
        catalog_rows.extend(_build_catalog_rows(normalized, dataset, factor_universe))
        long_frames.append(_build_long_rows(normalized, dataset, defaults_by_cancer))

    # Catalog frame (schema mirrors :mod:`.catalog`).
    catalog_schema = {
        "measure_id": pl.Utf8,
        "dataset": pl.Utf8,
        "dataset_label": pl.Utf8,
        "level": pl.Utf8,
        "category": pl.Utf8,
        "measure": pl.Utf8,
        "label": pl.Utf8,
        "unit": pl.Utf8,
        "source": pl.Utf8,
        "source_url": pl.Utf8,
        "state_value": pl.Float64,
        "factors": pl.Utf8,
        "is_numeric": pl.Boolean,
    }
    catalog_df = pl.DataFrame(catalog_rows, schema=catalog_schema).sort(
        ["dataset", "measure"]
    )

    # Long frame.
    if long_frames:
        long_df = pl.concat(long_frames, how="vertical_relaxed").sort(
            ["measure_id", "fips"]
        )
    else:
        long_df = pl.DataFrame(schema=LONG_SCHEMA)

    return catalog_df, long_df


#: Long-table schema with SCP extensions. Exported so ``pivot.py`` can
#: pad ECCO-sourced rows with nulls before concatenation.
LONG_SCHEMA: dict[str, pl.DataType] = {
    "fips": pl.Utf8,
    "measure_id": pl.Utf8,
    "value": pl.Float64,
    "value_str": pl.Utf8,
    "aac": pl.Float64,
    "value_lo": pl.Float64,
    "value_hi": pl.Float64,
    "trend_str": pl.Utf8,
    "trend_pct": pl.Float64,
    "trend_pct_lo": pl.Float64,
    "trend_pct_hi": pl.Float64,
    "rural_urban": pl.Utf8,
    "pct_late_stage": pl.Float64,
}

#: Datasets that ECCO used to publish for cancer rates/trends. The
#: snapshot orchestrator filters these out of the ECCO catalog before
#: building the long table, then concatenates SCP-sourced rows in their
#: place.
ECCO_CANCER_DATASETS: frozenset[str] = frozenset(
    {"scpincidence", "scpdeaths", "scpincidencetrend", "scpdeathstrend"}
)


def main() -> None:
    """Smoke-test: print row counts for the current release."""
    catalog_df, long_df = build_scp()
    print(f"catalog rows: {catalog_df.height}")
    print(f"long rows   : {long_df.height}")
    print("--- distinct measure_ids ---")
    for mid in sorted(catalog_df.get_column("measure_id").to_list())[:5]:
        print(f"  {mid}")
    print(f"  ... ({catalog_df.height} total)")


if __name__ == "__main__":
    main()
