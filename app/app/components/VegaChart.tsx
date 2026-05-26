"use client";

// Thin wrapper around vega-embed. Same shape as cfde-atlas's
// VegaChart: useEffect, embed, finalize on unmount, error state.

import { useEffect, useRef, useState } from "react";
import embed, { type Result, type VisualizationSpec } from "vega-embed";

type Props = {
  spec: unknown;
  className?: string;
  renderer?: "svg" | "canvas";
};

export default function VegaChart({ spec, className, renderer = "canvas" }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;

    let viewResult: Result | null = null;
    let cancelled = false;
    setError(null);

    embed(node, spec as VisualizationSpec, {
      actions: false,
      renderer,
      defaultStyle: true,
    })
      .then((result) => {
        if (cancelled) {
          result.finalize();
          return;
        }
        viewResult = result;
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      });

    return () => {
      cancelled = true;
      viewResult?.finalize();
    };
  }, [spec, renderer]);

  if (error) {
    return (
      <div className="text-xs text-red-600 dark:text-red-400 border border-red-400/30 rounded p-2 font-mono">
        Chart render failed: {error}
      </div>
    );
  }
  return <div ref={ref} className={className ?? "w-full overflow-x-auto"} />;
}
