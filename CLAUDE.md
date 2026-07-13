# CLAUDE.md

Operational guidance for working in this repo. Read `SPEC.md` for the full
architecture, data contract, and phased build plan. This file is the short version
plus the rules that are easy to get wrong.

## What this is

A static SPA (Vite + React + TypeScript) that explores Colorado cancer data
entirely in the browser using DuckDB-WASM over a precomputed Parquet snapshot, with
Vega-Lite for all visualizations (including the choropleth via `geoshape`). Data
comes from two public sources:

- **Cancer incidence & mortality** — the [SCP scraper release][scp] (monthly
  GitHub Release of NCI State Cancer Profiles data). Includes CIs on rate,
  inline trend with CI on slope, rural/urban classifier, and a US national
  anchor row at FIPS `00000`.
- **Everything else** (sociodemographics, screening, environment,
  disparities, etc.) — the public ECCO API (`https://api.coe-ecco.org`,
  GET-only, no auth).

An offline Python ETL (`etl/`) builds the snapshot. There is no application server.

[scp]: https://github.com/seandavi/state-cancer-profile-scraper

## Build in phases

Work phase by phase as defined in `SPEC.md` §7. Do not begin a phase until the
prior phase's acceptance criteria pass. Current foundation phase is **Phase 1
(ETL)** — everything else depends on its Parquet artifacts and the data contract in
`SPEC.md` §4.

## Commands

ETL (run from `etl/`):
```
uv sync
uv run python snapshot.py        # full snapshot → ../data/
uv run python verify.py          # acceptance checks
```

App (run from `app/`):
```
npm install
npm run dev                      # local dev server
npm run build                    # production build
npm run preview
```

## Rules that are easy to get wrong

1. **FIPS codes are strings with leading zeros.** Never parse them to integers
   anywhere — Python, DuckDB, JS, or CSV reads (force string dtype). Join keys
   across Parquet and TopoJSON must match exactly as strings.
2. **The catalog is the single source of truth** for measure labels, units,
   sources, and valid factor values. Never hardcode measure names or factor lists
   in the UI; read them from `catalog.parquet`.
3. **`unit` drives behavior.** Number formatting, legend type, and eligibility for
   correlation/clustering all derive from the measure's `unit` / `is_numeric`. Only
   numeric units (percent, rate, count, dollar_amount) may enter Pearson
   correlation or clustering.
4. **Expect nulls.** Cancer rates are suppressed for small counts. Correlation must
   be pairwise-complete; clustering must drop or impute. Choropleths render missing
   regions in a distinct "no data" color.
5. **Always show provenance.** Every view surfaces `source` and a linked
   `source_url` from the catalog.
6. **Snapshots are reproducible.** Sort rows deterministically before writing
   Parquet so re-runs are byte-stable. The ETL is idempotent.
7. **No secrets, no auth.** The ECCO API is public. Do not add credential handling.
8. **Be polite to the API** in ETL: concurrency ≤ 4, retry with backoff. Use
   `download-all` to bootstrap; prefer `fips-value` for per-measure metadata.

## Data contract

The Parquet schemas in `SPEC.md` §4 are a contract between `etl/` and `app/`.
Changing a column name means updating both sides and the spec in the same change.

## Unresolved

(none — `aac` meaning and the ECCO→SCP migration for cancer data are both
resolved in `SPEC.md` §9.)
