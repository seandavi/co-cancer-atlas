// Boot DuckDB-WASM in a Web Worker, register the snapshot Parquet files,
// and expose a typed query() helper. Initialization is cached behind a
// single promise so concurrent first calls share one boot sequence.
//
// The five parquet files (catalog, {county,tract}_{long,wide}) are
// fetched as ArrayBuffers and registered via registerFileBuffer, then
// queryable as plain table names from SQL.

import * as duckdb from "@duckdb/duckdb-wasm";

const SNAPSHOT_FILES = [
  "catalog.parquet",
  "county_long.parquet",
  "county_wide.parquet",
  "tract_long.parquet",
  "tract_wide.parquet",
] as const;

let connectionPromise: Promise<duckdb.AsyncDuckDBConnection> | null = null;

async function boot(): Promise<duckdb.AsyncDuckDBConnection> {
  const bundles = duckdb.getJsDelivrBundles();
  const bundle = await duckdb.selectBundle(bundles);

  // Inline a worker that pulls in the bundle's mainWorker script. This is
  // the standard workaround for cross-origin worker loading.
  const workerUrl = URL.createObjectURL(
    new Blob([`importScripts("${bundle.mainWorker!}");`], {
      type: "text/javascript",
    }),
  );

  const worker = new Worker(workerUrl);
  const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(), worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  URL.revokeObjectURL(workerUrl);

  // Fetch + register the snapshot in parallel.
  await Promise.all(
    SNAPSHOT_FILES.map(async (name) => {
      const response = await fetch(`./data/${name}`);
      if (!response.ok) {
        throw new Error(`fetch ${name} failed: HTTP ${response.status}`);
      }
      const buf = new Uint8Array(await response.arrayBuffer());
      await db.registerFileBuffer(name, buf);
    }),
  );

  const conn = await db.connect();

  // Bare-name views so SQL reads naturally:
  //   select count(*) from county_wide
  // rather than `from 'county_wide.parquet'`.
  for (const file of SNAPSHOT_FILES) {
    const view = file.replace(/\.parquet$/, "");
    await conn.query(
      `create or replace view ${view} as select * from '${file}'`,
    );
  }

  return conn;
}

/** Lazy, cached connection. First caller triggers boot; rest await. */
export function getConnection(): Promise<duckdb.AsyncDuckDBConnection> {
  if (!connectionPromise) {
    connectionPromise = boot();
  }
  return connectionPromise;
}

/** Run a SQL query and return rows as plain JS objects. */
export async function query<T = Record<string, unknown>>(
  sql: string,
): Promise<T[]> {
  const conn = await getConnection();
  const result = await conn.query(sql);
  return result.toArray().map((row) => row.toJSON()) as T[];
}

// Make the helper reachable from the devtools console — SPEC §7 Phase 2
// acceptance test is `query("select count(*) from county_wide")` returning 64.
if (typeof window !== "undefined") {
  (window as unknown as { query: typeof query }).query = query;
}
