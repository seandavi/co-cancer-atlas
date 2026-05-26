// Confirms DuckDB-WASM boots and the snapshot is reachable.
// GET /api/health → { counties, tracts, measures, snapshot_at }.

import { NextResponse } from "next/server";

import { queryAll } from "@/app/lib/db";

export const runtime = "nodejs";

export async function GET() {
  try {
    const [{ n: counties }] = await queryAll<{ n: number }>(
      "select count(*)::int as n from county_wide",
    );
    const [{ n: tracts }] = await queryAll<{ n: number }>(
      "select count(*)::int as n from tract_wide",
    );
    const [{ n: measures }] = await queryAll<{ n: number }>(
      "select count(*)::int as n from catalog",
    );

    return NextResponse.json({
      ok: true,
      counties,
      tracts,
      measures,
    });
  } catch (err) {
    return NextResponse.json(
      {
        ok: false,
        error: err instanceof Error ? err.message : String(err),
      },
      { status: 500 },
    );
  }
}
