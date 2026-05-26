import { tool } from "ai";
import { z } from "zod";

import { queryAll } from "@/app/lib/db";

export const listMeasures = tool({
  description:
    "Search the measure catalog by free-text. Returns a list of matching measures with their measure_id, label, category, level, unit, and is_numeric. Call this first whenever you're unsure which measure to use — never guess a measure_id.",
  inputSchema: z.object({
    query: z
      .string()
      .min(1)
      .describe(
        "Free-text search; matched case-insensitively against label, category, and dataset. Examples: 'lung cancer', 'breast incidence', 'poverty'.",
      ),
    level: z
      .enum(["county", "tract", "healthregion"])
      .optional()
      .describe(
        "Restrict to one geography level. Cancer measures are county-only. Sociodemographics and environment have both county and tract.",
      ),
    limit: z
      .number()
      .int()
      .min(1)
      .max(50)
      .default(20)
      .describe("Max number of matches to return."),
  }),
  execute: async ({ query, level, limit }) => {
    const q = query.replace(/'/g, "''").toLowerCase();
    const lvl = level ? `and level = '${level}'` : "";
    const rows = await queryAll<{
      measure_id: string;
      label: string;
      category: string;
      level: string;
      unit: string;
      is_numeric: boolean;
      factors: string;
    }>(
      `select measure_id, label, category, level, unit, is_numeric, factors
         from catalog
        where lower(label) like '%${q}%'
           or lower(category) like '%${q}%'
           or lower(dataset) like '%${q}%'
         ${lvl}
        order by level, dataset, measure
        limit ${limit}`,
    );
    return {
      query,
      level: level ?? "any",
      matches: rows.map((r) => ({
        measure_id: r.measure_id,
        label: r.label,
        category: r.category,
        level: r.level,
        unit: r.unit,
        is_numeric: r.is_numeric,
        // Send factor names only — the LLM rarely needs the full
        // values list to plan; describe_measure returns it.
        factor_names: Object.keys(JSON.parse(r.factors)),
      })),
    };
  },
});
