import { tool } from "ai";
import { z } from "zod";

import { queryAll } from "@/app/lib/db";

export const describeMeasure = tool({
  description:
    "Get the full catalog record for one or more measures by primary measure_id. Returns unit, source, source_url, state reference value, and the full factor definition (which factors the measure carries and what values each admits). Use this after list_measures to plan factor combinations.",
  inputSchema: z.object({
    measure_ids: z
      .array(z.string())
      .min(1)
      .max(10)
      .describe(
        "Primary measure_ids (no factor suffix). Example: ['scpincidence.All Cancer Sites'].",
      ),
  }),
  execute: async ({ measure_ids }) => {
    const escaped = measure_ids.map((id) => `'${id.replace(/'/g, "''")}'`);
    const inClause = `(${escaped.join(",")})`;
    const rows = await queryAll<{
      measure_id: string;
      dataset: string;
      level: string;
      category: string;
      label: string;
      unit: string;
      source: string;
      source_url: string;
      state_value: number | null;
      factors: string;
      is_numeric: boolean;
    }>(
      `select measure_id, dataset, level, category, label, unit,
              source, source_url, state_value, factors, is_numeric
         from catalog
        where measure_id in ${inClause}`,
    );

    const found = new Map(rows.map((r) => [r.measure_id, r]));
    return {
      measures: measure_ids.map((id) => {
        const r = found.get(id);
        if (!r) {
          return {
            measure_id: id,
            error: "Not in catalog. Did you mean a primary id from list_measures?",
          };
        }
        return {
          ...r,
          factors: JSON.parse(r.factors),
        };
      }),
    };
  },
});
