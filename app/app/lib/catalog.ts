// Typed Measure model + helpers used by tools.
// Keep this in sync with the Phase 1 catalog.parquet schema (SPEC §4).

import "server-only";

import { queryAll } from "./db";

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

/** Lazy, cached. Loads from catalog.parquet via DuckDB. */
export function getCatalog(): Promise<Measure[]> {
  if (!catalogPromise) {
    catalogPromise = (async () => {
      const rows = await queryAll<Omit<Measure, "factors"> & { factors: string }>(
        "select * from catalog order by level, dataset, measure",
      );
      return rows.map((r) => ({
        ...r,
        factors: JSON.parse(r.factors) as Record<string, FactorDefinition>,
      }));
    })();
  }
  return catalogPromise;
}

/** SPEC §4 composite id: primary + sorted `name=value` suffixes for non-defaults. */
export function measureIdFor(
  measure: Measure,
  values: Record<string, string>,
): string {
  const parts: string[] = [];
  for (const name of Object.keys(measure.factors).sort()) {
    const def = measure.factors[name];
    const v = values[name] ?? def.default;
    if (v !== def.default) parts.push(`${name}=${v}`);
  }
  if (parts.length === 0) return measure.measure_id;
  return `${measure.measure_id}#${parts.join(";")}`;
}

/** Find a measure by primary id (i.e. without factor suffix). */
export async function findMeasure(primaryId: string): Promise<Measure | null> {
  const all = await getCatalog();
  return all.find((m) => m.measure_id === primaryId) ?? null;
}

/** Formatters used by tools to render values consistently. */
const standardFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
const compactFmt = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});
const dollarFmt = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

export function formatValue(unit: MeasureUnit, value: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  switch (unit) {
    case "percent":
      return `${standardFmt.format(value)}%`;
    case "rate":
      return standardFmt.format(value);
    case "count":
      return compactFmt.format(value);
    case "dollar_amount":
      return dollarFmt.format(value);
    default:
      return standardFmt.format(value);
  }
}
