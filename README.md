# Colorado Cancer Atlas

A static SPA that explores Colorado cancer risk, incidence, mortality, and
the social/environmental determinants around them. All data comes from the
public [ECCO API](https://api.coe-ecco.org). The app runs entirely in the
browser using DuckDB-WASM over a precomputed Parquet snapshot, with
Vega-Lite driving every visualization (choropleths, correlation, scatter,
clustering). No server, no API keys.

**Read [`SPEC.md`](SPEC.md)** for the full architecture, data contract, and
phased build plan. **Read [`CLAUDE.md`](CLAUDE.md)** for the conventions
that are easy to get wrong (FIPS-as-string, catalog-is-authoritative,
unit-drives-behavior).

## Layout

```
etl/    Python ETL — pulls the ECCO API into ../data/ as Parquet + TopoJSON
app/    Vite + React + TypeScript SPA — reads the snapshot via DuckDB-WASM
data/   Generated artifacts (county-level committed; tract may move to R2)
mcp/    Optional Phase 7 MCP server (reads the same Parquet)
```

## Quick commands

ETL:
```bash
cd etl
uv sync
# Phase 1 (not yet implemented):
# uv run python -m co_cancer_atlas_etl.snapshot
# uv run python -m co_cancer_atlas_etl.verify
```

App:
```bash
cd app
npm install
npm run dev      # local dev
npm run build    # production build
```

## Current status

**Phase 0 — scaffold.** ETL and app skeletons in place; Phase 1 (the ETL
foundation) is the next deliverable. See `SPEC.md` §7.
