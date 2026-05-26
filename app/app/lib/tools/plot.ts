// Generic plotting tool. The LLM composes a Vega-Lite spec; the UI
// hands it to vega-embed.
//
// We don't pre-query data on the model's behalf — the model passes
// values inline (small datasets) or references a named view via the
// spec's `data` block. For map plots, use `render_choropleth` instead;
// it handles the TopoJSON join and suppressed-value coloring.

import { tool } from "ai";
import { z } from "zod";

export const plot = tool({
  description:
    "Render a Vega-Lite chart from a spec you compose. Use this for scatter plots, bar charts, line charts, histograms, heatmaps — anything that's not a Colorado map. Inline small datasets in spec.data.values; for larger ones, run query_data first and embed the rows you got back. Return the spec exactly as Vega-Lite expects (no $schema needed — the UI adds one). For maps of Colorado counties or tracts, use render_choropleth instead.",
  inputSchema: z.object({
    title: z
      .string()
      .describe(
        "Short title rendered above the chart (e.g. 'Smoking vs. lung cancer incidence, CO counties').",
      ),
    spec: z
      .record(z.string(), z.unknown())
      .describe(
        "A Vega-Lite v6 spec object. Include data (inline values), mark, encoding. The UI will inject $schema, sane width, and a light theme.",
      ),
    caption: z
      .string()
      .optional()
      .describe(
        "Optional one-line caption shown beneath the chart (e.g. data source, sample size, year range).",
      ),
  }),
  execute: async ({ title, spec, caption }) => {
    // We don't execute on the server — the UI renders. Returning a
    // structured envelope lets the ToolResult component identify the
    // payload and pass it to VegaChart.
    return {
      kind: "plot" as const,
      title,
      spec,
      caption,
    };
  },
});
