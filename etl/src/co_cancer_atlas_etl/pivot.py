"""Pull the long table from /fips-value, one call per (measure × factor combo).

The catalog is the single source of truth for what measures exist and
what factor values they admit (SPEC §3). For each catalog row, we
enumerate the Cartesian product of `factors.values` and ask the API
for each combination. Empty responses (some combos legitimately have
no data) are dropped. Non-empty rows go into the long table with the
SPEC §4 composite `measure_id`:

- primary series (all factors at their default): `{dataset}.{measure}`
- non-default factor combo: `{dataset}.{measure}#k1=v1;k2=v2` (sorted)

The wide table is the primary, numeric subset pivoted across regions.

Why not the bulk /stats/download-all dump? The zip pre-sanitizes
labels to filesystem-safe filenames (drops `<>/:`, decomposes `²`→`2`,
etc.), which costs us a multi-rule reverse mapping. Calling
fips-value keyed on the catalog's own measure ids is simpler, gives
us `aac` and `state` in the same response, and the API is small and
public — low-concurrency asyncio is fine.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import Iterable
from typing import Any

import polars as pl

from .fetch import AsyncEccoClient
from .catalog import factor_suffix


def _enumerate_factor_combos(
    factors: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """All factor-value combinations the API admits for a measure.

    Returns a list of filter dicts. The first entry is always the
    empty dict (the primary series — defaults). Non-default combos
    follow.
    """
    if not factors:
        return [{}]

    factor_names = sorted(factors.keys())
    value_lists = [list(factors[k]["values"].keys()) for k in factor_names]
    combos: list[dict[str, str]] = []
    for combo in itertools.product(*value_lists):
        combos.append(dict(zip(factor_names, combo, strict=True)))
    return combos


def _non_default_subset(
    factors_def: dict[str, dict[str, Any]],
    combo: dict[str, str],
) -> dict[str, str]:
    """Project a combo to only the factors that differ from their default."""
    return {
        k: v
        for k, v in combo.items()
        if v != factors_def[k]["default"]
    }


def _measure_id_for_combo(
    dataset: str,
    measure: str,
    factors_def: dict[str, dict[str, Any]],
    combo: dict[str, str],
) -> str:
    suffix = factor_suffix(_non_default_subset(factors_def, combo))
    return f"{dataset}.{measure}{suffix}"


def _coerce_value(raw: object) -> tuple[float | None, str | None]:
    """Split an API value into (numeric, string) — exactly one populated.

    Trend measures return categorical strings like "stable"; numeric
    measures return floats. Long table carries both so the SPA can
    surface either without re-typing.
    """
    if raw is None:
        return None, None
    if isinstance(raw, bool):
        return float(raw), None
    if isinstance(raw, (int, float)):
        return float(raw), None
    if isinstance(raw, str):
        try:
            return float(raw), None
        except ValueError:
            return None, raw
    return None, str(raw)


def _rows_from_response(
    dataset: str,
    measure: str,
    factors_def: dict[str, dict[str, Any]],
    combo: dict[str, str],
    resp: dict[str, Any],
) -> tuple[list[dict[str, Any]], float | None]:
    """Project one fips-value response into long rows + maybe a state value."""
    state_num: float | None = None
    if resp.get("state") is not None:
        value_num, _ = _coerce_value(resp["state"])
        state_num = value_num

    measure_id = _measure_id_for_combo(dataset, measure, factors_def, combo)
    rows: list[dict[str, Any]] = []
    for fips, payload in resp.get("values", {}).items():
        if not isinstance(payload, dict):
            continue
        raw_value = payload.get("value")
        raw_aac = payload.get("aac")
        if raw_value is None and raw_aac is None:
            continue
        value_num, value_str = _coerce_value(raw_value)
        aac_num, _ = _coerce_value(raw_aac)
        rows.append(
            {
                "fips": str(fips),
                "measure_id": measure_id,
                "value": value_num,
                "value_str": value_str,
                "aac": aac_num,
            }
        )
    return rows, state_num


async def build_long_for_level(
    client: AsyncEccoClient,
    catalog_df: pl.DataFrame,
    level: str,
    progress: bool = True,
) -> tuple[pl.DataFrame, dict[str, float]]:
    """Build long table + per-measure state values for one level.

    Enumerates every (measure × factor combo) up front and dispatches
    them all into one `asyncio.as_completed` pool. True throughput is
    bounded by the client's semaphore — a slow combo in one measure
    no longer stalls every other measure.

    Returns (long_df, state_by_measure_id). state_by_measure_id is
    keyed by the primary measure_id (state is a per-measure constant,
    not per factor combo).
    """
    level_rows = catalog_df.filter(pl.col("level") == level).to_dicts()

    tasks: list[tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, str]]] = []
    for cat_row in level_rows:
        factors_def: dict[str, dict[str, Any]] = json.loads(cat_row["factors"])
        for combo in _enumerate_factor_combos(factors_def):
            tasks.append((cat_row, factors_def, combo))

    if progress:
        print(
            f"[pivot:{level}] dispatching {len(tasks)} fetches "
            f"across {len(level_rows)} measures"
        )

    async def fetch_one(
        cat_row: dict[str, Any],
        factors_def: dict[str, dict[str, Any]],
        combo: dict[str, str],
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, str], dict[str, Any]]:
        resp = await client.fips_value(
            cat_row["dataset"],
            cat_row["measure"],
            level=level,
            filters=combo or None,
        )
        return cat_row, factors_def, combo, resp

    all_rows: list[dict[str, Any]] = []
    states: dict[str, float] = {}
    completed = 0
    total = len(tasks)
    coros = [fetch_one(c, f, k) for c, f, k in tasks]

    for done in asyncio.as_completed(coros):
        cat_row, factors_def, combo, resp = await done
        rows, state = _rows_from_response(
            cat_row["dataset"],
            cat_row["measure"],
            factors_def,
            combo,
            resp,
        )
        all_rows.extend(rows)
        if state is not None:
            states.setdefault(cat_row["measure_id"], state)
        completed += 1
        if progress and (completed % 500 == 0 or completed == total):
            print(
                f"[pivot:{level}] {completed}/{total} fetches "
                f"({len(all_rows)} rows)"
            )

    long_schema = {
        "fips": pl.Utf8,
        "measure_id": pl.Utf8,
        "value": pl.Float64,
        "value_str": pl.Utf8,
        "aac": pl.Float64,
    }
    if not all_rows:
        long_df = pl.DataFrame(schema=long_schema)
    else:
        long_df = pl.DataFrame(all_rows, schema=long_schema).sort(
            ["measure_id", "fips"]
        )
    return long_df, states


def build_wide(
    long_df: pl.DataFrame,
    catalog_df: pl.DataFrame,
    level: str,
    region_names: pl.DataFrame,
) -> pl.DataFrame:
    """Pivot the primary, numeric subset into a (fips, name, <measure_ids>) matrix."""
    primary_numeric_ids = set(
        catalog_df.filter(
            (pl.col("level") == level) & pl.col("is_numeric")
        )
        .get_column("measure_id")
        .to_list()
    )

    primary_long = long_df.filter(pl.col("measure_id").is_in(primary_numeric_ids))
    if primary_long.height == 0:
        return region_names

    wide = primary_long.pivot(
        on="measure_id",
        index="fips",
        values="value",
        aggregate_function="first",
    )
    return region_names.join(wide, on="fips", how="left").sort("fips")


def apply_state_values(
    catalog_df: pl.DataFrame, states_by_level: dict[str, dict[str, float]]
) -> pl.DataFrame:
    """Fill catalog `state_value` from per-level state lookups."""
    flat = {
        (level, mid): v
        for level, m in states_by_level.items()
        for mid, v in m.items()
    }
    return catalog_df.with_columns(
        pl.struct(["level", "measure_id"]).map_elements(
            lambda s: flat.get((s["level"], s["measure_id"])),
            return_dtype=pl.Float64,
        ).alias("state_value")
    )


# ----- helpers retained for fallback / scripts ---------------------------

def regions_from_iterable(
    regions: Iterable[tuple[str, str]],
) -> pl.DataFrame:
    """Build a (fips, name) frame from any iterable of (fips, name) pairs."""
    fips, names = zip(*regions, strict=True) if regions else ([], [])
    return pl.DataFrame(
        {"fips": list(fips), "name": list(names)},
        schema={"fips": pl.Utf8, "name": pl.Utf8},
    ).unique(subset=["fips"], keep="first").sort("fips")
