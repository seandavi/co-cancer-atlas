// Server-side DuckDB. Single instance per Node process, lazily booted.
// Snapshot Parquet (catalog + {county,tract}_{long,wide}) are registered
// as views once at boot and queryable by bare name.
//
// Uses @duckdb/node-api (native Node bindings) for dev + Node-hosted
// production. Cloudflare Workers deployment will need a WASM path
// instead — out of scope for this commit; we'll switch the boot
// function when wrangler dev is wired up.

import "server-only";

import { join } from "node:path";

import {
  DuckDBInstance,
  type DuckDBConnection,
} from "@duckdb/node-api";

const SNAPSHOT_FILES = [
  "catalog.parquet",
  "county_long.parquet",
  "county_wide.parquet",
  "tract_long.parquet",
  "tract_wide.parquet",
] as const;

let connectionPromise: Promise<DuckDBConnection> | null = null;

async function boot(): Promise<DuckDBConnection> {
  const db = await DuckDBInstance.create(":memory:");
  const conn = await db.connect();

  const dataDir = join(process.cwd(), "public", "data");
  for (const file of SNAPSHOT_FILES) {
    const view = file.replace(/\.parquet$/, "");
    // read_parquet keeps FIPS as Utf8 from the file's own schema, so
    // leading zeros are preserved.
    const path = join(dataDir, file).replace(/'/g, "''");
    await conn.run(
      `create or replace view ${view} as select * from read_parquet('${path}')`,
    );
  }
  return conn;
}

export function getConnection(): Promise<DuckDBConnection> {
  if (!connectionPromise) connectionPromise = boot();
  return connectionPromise;
}

/** Run a SELECT and return rows as plain JS objects. */
export async function queryAll<T = Record<string, unknown>>(
  sql: string,
): Promise<T[]> {
  const conn = await getConnection();
  const reader = await conn.runAndReadAll(sql);
  const rows = reader.getRowObjectsJson();
  return rows as T[];
}
