// Dedicated tool for Colorado choropleth maps. The TopoJSON join,
// factor-aware measure_id construction, "no data" coloring, and
// unit-aware tooltip formatting are all baked in.
//
// The tool executes the SQL to pull the row set, then returns both the
// data envelope (for the UI to render) and a ready-to-embed Vega-Lite
// spec. The UI's ToolResult component recognizes kind: "choropleth".

import { tool } from "ai";
import { z } from "zod";

import { queryAll } from "@/app/lib/db";
import {
  findMeasure,
  formatValue,
  measureIdFor,
  NUMERIC_UNITS,
  type Measure,
} from "@/app/lib/catalog";

type LongRow = {
  fips: string;
  value: number | null;
  value_str: string | null;
  aac: number | null;
};

const TOPO_BY_LEVEL = {
  // Mapshaper names the TopoJSON object after the input filename.
  county: { url: "/data/co_counties.topojson", feature: "co_counties", height: 480 },
  tract: { url: "/data/co_tracts.topojson", feature: "co_tracts", height: 600 },
} as const;

export const renderChoropleth = tool({
  description:
    "Render a Colorado choropleth (county or tract) for one measure. Pass the primary measure_id from list_measures and any non-default factor values you want (e.g. sex='Female'). Suppressed regions render in a distinct 'no data' color. Tooltip shows the region name, formatted value, and average annual count for cancer measures.",
  inputSchema: z.object({
    measure_id: z
      .string()
      .describe("Primary measure_id (no factor suffix). Get it from list_measures."),
    factor_values: z
      .record(z.string(), z.string())
      .optional()
      .describe(
        "Non-default factor values keyed by factor name, e.g. {sex: 'Female', race: 'Black (Non-Hispanic)'}. Factors not given fall to their catalog default. Get the factor schema from describe_measure.",
      ),
    title: z
      .string()
      .optional()
      .describe(
        "Optional custom chart title. Defaults to the measure label plus any non-default factor values.",
      ),
  }),
  execute: async ({ measure_id, factor_values, title }) => {
    const measure = await findMeasure(measure_id);
    if (!measure) {
      return {
        error: `Unknown measure_id: ${measure_id}. Use list_measures to find a valid id.`,
      };
    }
    const factors = factor_values ?? {};
    const fullId = measureIdFor(measure, factors);
    const topo = TOPO_BY_LEVEL[measure.level as keyof typeof TOPO_BY_LEVEL];
    if (!topo) {
      return {
        error: `Choropleth rendering is supported for county and tract levels; ${measure.level} is not.`,
      };
    }

    const safeId = fullId.replace(/'/g, "''");
    // Cancer measures (scpincidence / scpdeaths) carry a national anchor
    // row at FIPS 00000 — useful for chat-time comparison, but it would
    // stretch the color scale and inflate the region counts here. Filter
    // to Colorado FIPS (08***) only.
    const rows = await queryAll<LongRow>(
      `select fips, value, value_str, aac
         from ${measure.level}_long
        where measure_id = '${safeId}'
          and fips like '08%'`,
    );

    if (rows.length === 0) {
      return {
        error: `No data for ${fullId}. The factor combination may not be published for this measure.`,
        fullId,
      };
    }

    const spec = buildChoroplethSpec({
      measure,
      fullId,
      title: title ?? defaultTitle(measure, factors),
      rows,
      topo,
    });

    return {
      kind: "choropleth" as const,
      measure_id: fullId,
      title: title ?? defaultTitle(measure, factors),
      level: measure.level,
      regions_reporting: rows.filter(
        (r) => r.value !== null || r.value_str !== null,
      ).length,
      regions_total: rows.length,
      source: measure.source || null,
      source_url: measure.source_url || null,
      state_value: measure.state_value,
      unit: measure.unit,
      spec,
    };
  },
});

function defaultTitle(measure: Measure, factors: Record<string, string>): string {
  const nonDefault = Object.entries(factors).filter(
    ([k, v]) => measure.factors[k] && v !== measure.factors[k].default,
  );
  if (nonDefault.length === 0) return measure.label;
  const suffix = nonDefault.map(([k, v]) => `${k}: ${v}`).join(", ");
  return `${measure.label} (${suffix})`;
}

function buildChoroplethSpec({
  measure,
  fullId,
  title,
  rows,
  topo,
}: {
  measure: Measure;
  fullId: string;
  title: string;
  rows: LongRow[];
  topo: { url: string; feature: string; height: number };
}) {
  const isNumeric = NUMERIC_UNITS.has(measure.unit);

  const lookupValues = rows.map((r) => ({
    fips: r.fips,
    value: r.value,
    value_str: r.value_str,
    aac: r.aac,
    formatted:
      isNumeric && r.value !== null
        ? formatValue(measure.unit, r.value)
        : (r.value_str ?? "—"),
  }));

  const colorEncoding = isNumeric
    ? {
        field: "value",
        type: "quantitative",
        scale: { scheme: "blues" },
        legend: {
          title: measure.unit === "percent" ? "%" : measure.unit,
          orient: "right",
          gradientLength: 200,
        },
      }
    : {
        field: "value_str",
        type: "nominal",
        scale: { scheme: "tableau10" },
        legend: { title: measure.label, orient: "right" },
      };

  const tooltip: Array<Record<string, unknown>> = [
    { field: "properties.name", type: "nominal", title: "Region" },
    { field: "properties.fips", type: "nominal", title: "FIPS" },
    { field: "formatted", type: "nominal", title: measure.label },
  ];
  if (rows.some((r) => r.aac !== null)) {
    tooltip.push({
      field: "aac",
      type: "quantitative",
      format: ",d",
      title: "Avg. annual count",
    });
  }

  return {
    $schema: "https://vega.github.io/schema/vega-lite/v6.json",
    title,
    width: 640,
    height: topo.height,
    autosize: { type: "fit", contains: "padding", resize: true },
    background: null,
    config: { view: { stroke: null } },
    data: { url: topo.url, format: { type: "topojson", feature: topo.feature } },
    transform: [
      {
        lookup: "id",
        from: {
          data: { values: lookupValues },
          key: "fips",
          fields: ["value", "value_str", "aac", "formatted"],
        },
      },
    ],
    // Hand-fit projection to Colorado. Vega-Lite's default projection
    // auto-fit isn't kicking in for this topojson (likely because the
    // lookup transform introduces a join the fit code doesn't follow).
    // Empirical: scale ≈ width × 9 with the state center gives a snug
    // fit for both 640-wide county maps and the tract variant.
    projection: {
      type: "mercator",
      center: [-105.55, 39.0],
      scale: 640 * 9,
    },
    mark: {
      type: "geoshape",
      stroke: "#ffffff",
      strokeWidth: measure.level === "tract" ? 0.1 : 0.5,
    },
    encoding: {
      color: {
        condition: {
          test: isNumeric ? "!isValid(datum.value)" : "!isValid(datum.value_str)",
          value: "#dcdce0",
        },
        ...colorEncoding,
      },
      tooltip,
    },
    _measure_id: fullId,
  };
}
