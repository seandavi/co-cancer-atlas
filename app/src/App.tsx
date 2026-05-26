import { useEffect, useState } from "react";
import {
  getCatalog,
  groupByCategory,
  type Measure,
} from "./data/catalog";
import "./App.css";

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; measures: Measure[] }
  | { kind: "error"; message: string };

function App() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const measures = await getCatalog();
        if (!cancelled) setState({ kind: "ready", measures });
      } catch (err) {
        if (!cancelled) {
          setState({
            kind: "error",
            message: err instanceof Error ? err.message : String(err),
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.kind === "loading") {
    return (
      <main>
        <h1>Colorado Cancer Atlas</h1>
        <p>Loading catalog…</p>
      </main>
    );
  }
  if (state.kind === "error") {
    return (
      <main>
        <h1>Colorado Cancer Atlas</h1>
        <p className="error">Failed to load catalog: {state.message}</p>
      </main>
    );
  }

  const grouped = groupByCategory(state.measures);
  const counts = state.measures.reduce(
    (acc, m) => {
      acc[m.level] = (acc[m.level] ?? 0) + 1;
      return acc;
    },
    {} as Record<string, number>,
  );

  return (
    <main>
      <h1>Colorado Cancer Atlas</h1>
      <p className="subtitle">
        {state.measures.length} measures —{" "}
        {Object.entries(counts)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([level, n]) => `${n} ${level}`)
          .join(", ")}
      </p>

      {Array.from(grouped.entries()).map(([level, categories]) => (
        <section key={level} className="level-block">
          <h2>{level}</h2>
          {Array.from(categories.entries()).map(([category, measures]) => (
            <details key={category}>
              <summary>
                {category}
                <span className="badge">{measures.length}</span>
              </summary>
              <ul>
                {measures.map((m) => (
                  <li key={m.measure_id}>
                    <span className="label">{m.label}</span>
                    <span className="unit">{m.unit}</span>
                  </li>
                ))}
              </ul>
            </details>
          ))}
        </section>
      ))}
    </main>
  );
}

export default App;
