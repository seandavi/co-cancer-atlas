import { tool } from "ai";
import { z } from "zod";

import { queryAll } from "@/app/lib/db";

const FORBIDDEN = /\b(insert|update|delete|drop|create|alter|attach|copy|pragma|export|import|load|install)\b/i;
const ROW_CAP = 500;

export const queryData = tool({
  description:
    "Run a SELECT against the snapshot. Available views: catalog, county_long, county_wide, tract_long, tract_wide. Read SPEC §4 schemas via list_measures / describe_measure if unsure. SELECT only — INSERT/UPDATE/DROP/etc. are rejected. Returns at most 500 rows; add a LIMIT for exploratory queries.",
  inputSchema: z.object({
    sql: z
      .string()
      .min(1)
      .describe(
        "A single SELECT statement. Use double-quotes around column names that contain dots — measure_id columns in the wide tables look like \"scpincidence.All Cancer Sites\".",
      ),
  }),
  execute: async ({ sql }) => {
    const stripped = sql.trim().replace(/;+\s*$/, "");
    if (!stripped.toLowerCase().startsWith("select") && !stripped.toLowerCase().startsWith("with")) {
      return { error: "Only SELECT (or WITH) queries are allowed." };
    }
    if (FORBIDDEN.test(stripped)) {
      return { error: "Query contains a forbidden keyword (write/DDL)." };
    }
    try {
      const rows = await queryAll(stripped);
      if (rows.length > ROW_CAP) {
        return {
          error: `Result exceeded ${ROW_CAP} rows. Add a LIMIT or aggregate.`,
          row_count: rows.length,
        };
      }
      return { row_count: rows.length, rows };
    } catch (err) {
      return {
        error: err instanceof Error ? err.message : String(err),
      };
    }
  },
});
