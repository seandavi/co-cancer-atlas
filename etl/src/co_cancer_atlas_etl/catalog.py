"""Build catalog.parquet from /stats/measures.

The catalog is the single source of truth for measure labels, units,
sources, and factor definitions (SPEC §3, §4). Everything downstream —
the SPA's MeasurePicker, the unit-aware formatters, the
is_numeric-gating for correlation/clustering — keys off this file.

The composite `measure_id` rule from SPEC §4: for the primary series
(all factors at their default), `measure_id = "{dataset}.{measure}"`.
Non-default factor combinations append a sorted suffix later in
pivot.py — this file emits one row per measure (the primary series).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

# Units that admit Pearson correlation and clustering (SPEC §3).
NUMERIC_UNITS: frozenset[str] = frozenset(
    {"percent", "rate", "count", "dollar_amount"}
)

# Schema mirrors SPEC §4 `catalog.parquet`, plus `dataset_label` for
# joining to the download-all CSVs (whose path uses the human label).
SCHEMA: dict[str, pl.DataType] = {
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
    "factors": pl.Utf8,  # JSON-serialized
    "is_numeric": pl.Boolean,
}


def primary_measure_id(dataset: str, measure: str) -> str:
    """SPEC §4 composite key for the primary (default-factor) series."""
    return f"{dataset}.{measure}"


def factor_suffix(non_default: dict[str, str]) -> str:
    """Build the SPEC §4 suffix for a non-default factor combination.

    Sorted by factor key for stability:
        {'sex': 'Female', 'race': 'Black NH'} -> '#race=Black NH;sex=Female'
    """
    if not non_default:
        return ""
    parts = [f"{k}={v}" for k, v in sorted(non_default.items())]
    return "#" + ";".join(parts)


def build_catalog(raw: dict[str, Any]) -> pl.DataFrame:
    """Walk /stats/measures JSON → flat Polars frame, one row per measure.

    Pure function over the raw catalog dict; no network. Sorted
    deterministically by (level, dataset, measure).
    """
    rows: list[dict[str, Any]] = []
    for level, level_block in raw.items():
        if not isinstance(level_block, dict):
            continue
        for dataset_key, dataset_block in level_block.get("categories", {}).items():
            dataset_label = dataset_block.get("label", dataset_key)
            for measure_key, meta in dataset_block.get("measures", {}).items():
                unit = meta.get("unit") or ""
                factors_obj = meta.get("factors") or {}
                rows.append(
                    {
                        "measure_id": primary_measure_id(dataset_key, measure_key),
                        "dataset": dataset_key,
                        "dataset_label": dataset_label,
                        "level": level,
                        "category": dataset_label,
                        "measure": measure_key,
                        "label": meta.get("label") or measure_key,
                        "unit": unit,
                        "source": meta.get("source") or "",
                        "source_url": meta.get("source_url") or "",
                        "state_value": None,
                        "factors": json.dumps(
                            factors_obj, sort_keys=True, ensure_ascii=False
                        ),
                        "is_numeric": unit in NUMERIC_UNITS,
                    }
                )

    return (
        pl.DataFrame(rows, schema=SCHEMA)
        .sort(by=["level", "dataset", "measure"])
    )


def write_catalog(df: pl.DataFrame, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out_path, compression="zstd", statistics=True)


async def main(out_path: Path | None = None) -> Path:
    """Fetch the live catalog and write catalog.parquet."""
    from .fetch import AsyncEccoClient

    if out_path is None:
        out_path = Path(__file__).resolve().parents[3] / "data" / "catalog.parquet"

    async with AsyncEccoClient() as ecco:
        raw = await ecco.catalog()
    df = build_catalog(raw)
    write_catalog(df, out_path)
    return out_path


if __name__ == "__main__":
    import asyncio

    path = asyncio.run(main())
    print(f"wrote {path}")
