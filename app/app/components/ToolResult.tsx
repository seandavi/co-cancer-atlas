"use client";

// Renders the structured output of a tool call inline in the chat.
// Knows the kinds we emit: "plot" and "choropleth". Falls back to a
// collapsed JSON view for everything else (list_measures, query_data,
// describe_measure — useful while debugging).

import VegaChart from "./VegaChart";

type Props = {
  toolName: string;
  output: unknown;
};

export default function ToolResult({ toolName, output }: Props) {
  if (output && typeof output === "object" && "error" in output) {
    return (
      <div className="text-xs text-red-700 dark:text-red-400 border border-red-300/40 dark:border-red-800/40 rounded p-2 my-2 font-mono">
        <span className="font-semibold">{toolName} error:</span>{" "}
        {String((output as { error: unknown }).error)}
      </div>
    );
  }

  if (isChoropleth(output)) {
    return (
      <figure className="my-3 border border-slate-200 dark:border-slate-800 rounded-lg p-3 bg-white dark:bg-slate-900">
        <VegaChart spec={output.spec} />
        <figcaption className="mt-2 text-xs text-slate-500 dark:text-slate-400 flex flex-wrap gap-3 items-baseline">
          <span>
            {output.regions_reporting} / {output.regions_total} {output.level}s
            reporting
          </span>
          {output.state_value !== null && output.state_value !== undefined && (
            <span>· Colorado: {output.state_value}</span>
          )}
          {output.source && (
            <span>
              · Source:{" "}
              {output.source_url ? (
                <a
                  href={output.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="underline decoration-dotted"
                >
                  {output.source}
                </a>
              ) : (
                output.source
              )}
            </span>
          )}
        </figcaption>
      </figure>
    );
  }

  if (isPlot(output)) {
    return (
      <figure className="my-3 border border-slate-200 dark:border-slate-800 rounded-lg p-3 bg-white dark:bg-slate-900">
        {output.title && (
          <figcaption className="text-sm font-medium mb-2 text-slate-700 dark:text-slate-200">
            {output.title}
          </figcaption>
        )}
        <VegaChart spec={output.spec} />
        {output.caption && (
          <figcaption className="mt-2 text-xs text-slate-500 dark:text-slate-400">
            {output.caption}
          </figcaption>
        )}
      </figure>
    );
  }

  return (
    <details className="my-2 text-xs">
      <summary className="cursor-pointer text-slate-500 dark:text-slate-400">
        {toolName} result
      </summary>
      <pre className="mt-1 p-2 bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded overflow-x-auto text-[11px]">
        {JSON.stringify(output, null, 2)}
      </pre>
    </details>
  );
}

type ChoroplethResult = {
  kind: "choropleth";
  spec: unknown;
  level: string;
  regions_reporting: number;
  regions_total: number;
  state_value: number | null;
  source: string | null;
  source_url: string | null;
};
function isChoropleth(o: unknown): o is ChoroplethResult {
  return (
    typeof o === "object" &&
    o !== null &&
    (o as { kind?: unknown }).kind === "choropleth"
  );
}

type PlotResult = {
  kind: "plot";
  title?: string;
  spec: unknown;
  caption?: string;
};
function isPlot(o: unknown): o is PlotResult {
  return (
    typeof o === "object" &&
    o !== null &&
    (o as { kind?: unknown }).kind === "plot"
  );
}
