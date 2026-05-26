// Load and type the measure catalog. Mirrors SPEC §4 catalog.parquet.
// Everything downstream (MeasurePicker, viz specs, unit-aware formatters)
// keys off this file.

import { query } from "../db/duckdb";

export type MeasureUnit =
  | "percent"
  | "rate"
  | "count"
  | "dollar_amount"
  | "rank"
  | "ordinal"
  | "categorical"
  | "least_most";

export type Level = "county" | "tract" | "healthregion";

export type FactorDefinition = {
  label: string;
  default: string;
  values: Record<string, string>;
};

export type Measure = {
  measure_id: string;
  dataset: string;
  dataset_label: string;
  level: Level;
  category: string;
  measure: string;
  label: string;
  unit: MeasureUnit;
  source: string;
  source_url: string;
  state_value: number | null;
  /** Empty object when the measure has no factors. */
  factors: Record<string, FactorDefinition>;
  is_numeric: boolean;
};

/** Units that admit Pearson correlation and clustering (SPEC §3). */
export const NUMERIC_UNITS: ReadonlySet<MeasureUnit> = new Set<MeasureUnit>([
  "percent",
  "rate",
  "count",
  "dollar_amount",
]);

let catalogPromise: Promise<Measure[]> | null = null;

/** Lazy-loaded full catalog, ordered by (level, dataset, measure). */
export function getCatalog(): Promise<Measure[]> {
  if (!catalogPromise) {
    catalogPromise = (async () => {
      const rows = await query<{
        measure_id: string;
        dataset: string;
        dataset_label: string;
        level: Level;
        category: string;
        measure: string;
        label: string;
        unit: MeasureUnit;
        source: string;
        source_url: string;
        state_value: number | null;
        factors: string;
        is_numeric: boolean;
      }>(
        "select * from 'catalog.parquet' order by level, dataset, measure",
      );
      return rows.map((r) => ({
        ...r,
        factors: JSON.parse(r.factors) as Record<string, FactorDefinition>,
      }));
    })();
  }
  return catalogPromise;
}

/** Group measures by (level, category) → array of measures. */
export function groupByCategory(
  measures: readonly Measure[],
): Map<string, Map<string, Measure[]>> {
  const byLevel = new Map<string, Map<string, Measure[]>>();
  for (const m of measures) {
    let byCategory = byLevel.get(m.level);
    if (!byCategory) {
      byCategory = new Map();
      byLevel.set(m.level, byCategory);
    }
    let bucket = byCategory.get(m.category);
    if (!bucket) {
      bucket = [];
      byCategory.set(m.category, bucket);
    }
    bucket.push(m);
  }
  return byLevel;
}

// ----- unit-aware formatters --------------------------------------------
//
// `unit` drives display in tooltips, legends, and provenance panels.
// Returns "—" for nulls so the SPA doesn't render literal "NaN".

const compactNumber = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

const standardNumber = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 1,
});

const dollarNumber = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

export function formatValue(unit: MeasureUnit, value: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  switch (unit) {
    case "percent":
      // The API returns percents as 0-100, not 0-1.
      return `${standardNumber.format(value)}%`;
    case "rate":
      // Cancer rates are per 100k; other rates are also unitless ratios.
      // The catalog doesn't distinguish — surface the bare number with
      // a suffix the SPA layout can add in context if needed.
      return standardNumber.format(value);
    case "count":
      return compactNumber.format(value);
    case "dollar_amount":
      return dollarNumber.format(value);
    case "rank":
    case "ordinal":
    case "least_most":
    case "categorical":
      return standardNumber.format(value);
  }
}
