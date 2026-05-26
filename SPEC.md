# Colorado Cancer Atlas — Build Specification

A static single-page application for exploring Colorado cancer risk, incidence,
mortality, and the social/environmental determinants around them. All data comes
from the public **ECCO API** (`https://api.coe-ecco.org`). The app runs entirely
in the browser using **DuckDB-WASM** over a precomputed **Parquet** snapshot, with
**Vega-Lite** driving every visualization. No application server, no API keys, no
database to operate.

This document is the contract. It is written so the work can proceed in discrete,
independently verifiable phases. Build phases in order; each has explicit
deliverables and acceptance criteria.

---

## 1. Goals & non-goals

**Goals**
- Choropleth maps of any measure across Colorado counties (and census tracts).
- Correlation exploration: measure-vs-measure across regions (heatmap + drill-down scatter).
- Grouping/clustering: segment counties by measure profiles or by quantiles of a chosen measure, then compare cancer outcomes across groups.
- Every value traceable to its source (the catalog carries `source` / `source_url`).
- Fully static deploy; reproducible data snapshot.

**Non-goals (v1)**
- No write-back, accounts, or per-user state on a server.
- No live per-interaction calls to the ECCO API from the browser (the snapshot is the source of truth at runtime).
- No tract-level clustering in v1 if it proves too sparse — county-level is the priority surface.

---

## 2. Architecture

Four layers. The first three are v1; the fourth is a sibling that reuses the same snapshot.

```
ECCO API ──▶ [1] ETL / snapshot (Python, offline)
                     │  pull → pivot → Parquet + catalog + TopoJSON
                     ▼
              [2] Data artifacts (static files)
                     │  county/tract long + wide matrices, catalog, geometry
                     ├────────────────────────────┐
                     ▼                             ▼
              [3] SPA (DuckDB-WASM + Vega-Lite)   [4] MCP server (optional, later)
                  browser-only, reads Parquet         reads the same Parquet
```

The pivotal design fact: **this is small data.** Colorado is 64 counties and
~1,450 tracts; total measures number in the low hundreds. The county matrix is on
the order of 64 × few-hundred. Correlation matrices, k-means, and hierarchical
clustering are instant and can run client-side. That is what makes the
zero-backend WASM approach viable.

---

## 3. Data source: the ECCO API

GET-only, unauthenticated. Interactive docs at `/docs`. Relevant endpoints:

**Geography**
- `GET /counties` — county metadata + GeoJSON geometry (in `wkb_geometry`). Keys: `cnty_fips`, `us_fips`, `county`, `cent_lat`, `cent_long`.
- `GET /tracts` — census tracts (same shape, tract FIPS).
- `GET /healthregions` — CDPHE health statistics regions (integer IDs, `counties` membership string).

**Stats** — each dataset exposes a uniform set of routes:
- `GET /stats/{level}/{dataset}/measures` → list of measure names.
- `GET /stats/{level}/{dataset}` → paginated raw rows (`measure`, `page`, `size`≤100).
- `GET /stats/{level}/{dataset}/fips-value?measure=…&filters=…` → **pivot-friendly**: `{min, max, state, state_source, unit, source, source_url, order, values: {FIPS: {value, aac}}}`.
- `GET /stats/{level}/{dataset}/as-csv?measure=…` → CSV.

**Catalog / convenience**
- `GET /stats/measures` → master catalog: every measure with `label`, `unit`, `source`, `source_url`, and `factors` (allowed values + defaults).
- `GET /stats/by-county/{fips}` → everything for one county, nested by category.
- `GET /stats/download-all` → **zip of every stats table as CSV — primary value source for the snapshot.** ~16 MB, ~290 CSVs. Each CSV row already includes the factor columns (`sex`, `stage`, `race`, `age`) pre-expanded, which is the long-format shape we want. **Does not include** `aac` or per-measure `state`; for those, follow up with `fips-value` on the cancer datasets only.

**Datasets by level** (verify against live `/stats/measures` at build time):
- County: `scpincidence`, `scpdeaths`, `scpincidencetrend`, `scpdeathstrend`, `cancerdisparitiesindex`, `rfandscreening`, `hpv`, `radon`, `uvexposure`, `disparities`, `sociodemographics`, `economy`, `environment`, `housingtrans`, `ucccresponders`.
- Tract: `sociodemographics`, `economy`, `environment`, `fooddesert`, `housingtrans`, `rfandscreening`, `disparities`, `radon`.
- Health region: `vaping`.

### API gotchas (must be respected in ETL)
- **FIPS are strings with leading zeros.** Never parse to int anywhere — not in Python, not in DuckDB, not in JS. Read CSVs with FIPS columns forced to string.
- **Hybrid pull strategy.** `download-all` is the primary source for values (one request, all measures, all factor combinations pre-expanded as long rows). `fips-value` is the secondary enrichment pass, called only for the cancer datasets (`scpincidence`, `scpdeaths`, `scpincidencetrend`, `scpdeathstrend`) where we need `aac` for the tooltip and `state` for the reference annotation. The catalog (`/stats/measures`) supplies `label`, `unit`, `source`, `source_url`, and factor definitions — those don't need to come from `fips-value`.
- **`aac` companion field**: present alongside each `value`. Confirmed as the **average annual count** accompanying the age-adjusted rate. Verified Phase 1.0 against State Cancer Profiles for Denver County / All Cancer Sites incidence (ECCO 404.1 rate / 2746 aac vs SCP 400.3 rate / 2765 aac — within 1%, attributable to year-range differences between the two snapshots). See `etl/src/co_cancer_atlas_etl/aac_probe.py` for the re-runnable check.
- **Factors**: some measures (e.g. HPV has `sex`, several have `RE`/`Sex`/`Age`) expand into multiple series. The catalog defines allowed values and the default. See §4 for how factors map into the data model.
- **Suppressed / missing values**: cancer rates are frequently suppressed for small counts → expect nulls. Correlation must be pairwise-complete; clustering must impute or drop.
- **Units gate visualizations.** `MeasureUnit` ∈ {percent, count, rate, dollar_amount, rank, ordinal, categorical, least_most}. Only continuous numeric units (percent, rate, count, dollar_amount) are valid for Pearson correlation and clustering. Categorical/ordinal/rank measures are choropleth-only.
- **Be polite**: cap ETL concurrency (≤4), add retry with backoff. The snapshot runs rarely.

---

## 4. Data contract (Parquet schemas)

These schemas are the agreement between ETL and the SPA. Do not change column names
without updating both sides and this section.

### `catalog.parquet`
One row per `measure_id`. Drives every dropdown, label, number format, provenance panel, and viz-gating decision.

| column | type | notes |
|---|---|---|
| `measure_id` | string | stable key; see composite rule below |
| `dataset` | string | e.g. `scpincidence` |
| `level` | string | `county` \| `tract` \| `healthregion` |
| `category` | string | from catalog grouping (e.g. "Cancer Incidence") |
| `measure` | string | raw measure name from the API |
| `label` | string | human label from catalog |
| `unit` | string | the `MeasureUnit` enum value |
| `source` | string | |
| `source_url` | string | |
| `state_value` | double | state reference value (nullable) |
| `factors` | string (JSON) | `{factor: {label, default, values:{code:label}}}`, `{}` if none |
| `is_numeric` | bool | true when unit ∈ {percent, rate, count, dollar_amount} |

**Composite `measure_id` rule:** for factorless measures, `measure_id = "{dataset}.{measure}"`. For measures with factors, the **primary series** (all factors at their default) uses the same base id; **non-default factor combinations** append a sorted suffix: `"{dataset}.{measure}#RE=White NH;Sex=Female"`. The wide matrix (below) contains only primary series by default; the long table contains all.

### `{level}_long.parquet`  (`county_long`, `tract_long`)
Tidy long; the complete record including all factor combinations.

| column | type | notes |
|---|---|---|
| `fips` | string | region key (county/tract FIPS; health-region id as string) |
| `measure_id` | string | FK to catalog (includes factor suffix where applicable) |
| `value` | double | nullable (suppressed → null) |
| `aac` | double | nullable; average annual count (verified Phase 1.0) |

### `{level}_wide.parquet`  (`county_wide`, `tract_wide`)
Pivoted primary-series matrix for correlation/clustering. One row per region.

| column | type | notes |
|---|---|---|
| `fips` | string | region key |
| `name` | string | region name (joined from geography) |
| `<measure_id>` | double | one column per **primary, numeric** `measure_id`; nullable |

Only `is_numeric` primary series become columns. Column names are the `measure_id`
strings verbatim (quote them in SQL).

### Geometry
- `co_counties.topojson`, `co_tracts.topojson` — TopoJSON converted from the API's GeoJSON (smaller; Vega-Lite reads TopoJSON natively). Feature id property must be the FIPS string matching `fips` above.

### Hosting
County-level Parquet + TopoJSON are small — commit to the repo and serve as static
assets (fetch → `registerFileBuffer` in DuckDB-WASM). If tract artifacts grow past
a few MB, host them on Cloudflare R2 and fetch with CORS enabled; range-based
httpfs reads are an optional later optimization.

---

## 5. Tech stack

**ETL** — Python 3.12+, managed with `uv`. Deps: `httpx`, `duckdb`, `pyarrow`,
`topojson` (or `mapshaper` via npx for GeoJSON→TopoJSON). DuckDB does the pivot
(`PIVOT`/`pivot_wider`) and writes Parquet directly.

**SPA** — Vite + React 18+ + TypeScript. Core deps:
- `@duckdb/duckdb-wasm` — in-browser query engine.
- `vega`, `vega-lite`, `vega-embed` — **all four view types** including the choropleth via `geoshape` (no separate map library, no tile provider, no key).
- A small clustering helper (`ml-kmeans` or a ~40-line k-means in `lib/stats.ts`); standardization and correlation are hand-rolled or done in DuckDB SQL.
- Styling: minimal CSS modules or Tailwind — implementer's choice, keep it light.

Pin exact versions in lockfiles; the libraries above move quickly, so resolve
"latest stable" at scaffold time rather than trusting any version quoted here.

**Deploy** — static host (Cloudflare Pages or GitHub Pages). GitHub Actions for
(a) periodic ETL refresh and (b) build+deploy.

**MCP (Phase 7)** — Python `FastMCP`, reads the same Parquet via DuckDB.

---

## 6. Repository layout

```
co-cancer-atlas/
  README.md
  SPEC.md                      # this document
  CLAUDE.md                    # conventions + commands for Claude Code
  etl/
    pyproject.toml
    config.toml                # base url, datasets, levels, concurrency
    snapshot.py                # orchestrates a full snapshot
    fetch.py                   # ECCO client (paged + fips-value + download-all)
    catalog.py                 # build catalog.parquet from /stats/measures
    pivot.py                   # long → wide via DuckDB
    geo.py                     # GeoJSON → TopoJSON, id = fips
    verify.py                  # acceptance checks (row counts, aac spot-check)
  data/                        # generated artifacts (county-level committed)
    catalog.parquet
    county_long.parquet
    county_wide.parquet
    tract_long.parquet
    tract_wide.parquet
    co_counties.topojson
    co_tracts.topojson
  app/
    package.json
    vite.config.ts
    index.html
    src/
      main.tsx
      db/duckdb.ts             # wasm init, registerFileBuffer, query() helper
      data/catalog.ts          # load catalog, typed Measure model, formatters
      state/store.ts           # shared selection (active measures, region, group)
      lib/stats.ts             # standardize, pearson/spearman, kmeans
      viz/specs.ts             # Vega-Lite spec builders (one per view)
      views/
        ChoroplethView.tsx
        CorrelationView.tsx
        ScatterView.tsx
        GroupingView.tsx
      components/
        MeasurePicker.tsx
        FactorSelector.tsx
        ProvenancePanel.tsx
        Layout.tsx
  mcp/                         # Phase 7
    server.py
  .github/workflows/
    etl.yml
    deploy.yml
```

---

## 7. Build components (phased)

Each phase is a self-contained component. Do not start a phase until the prior
phase's acceptance criteria pass.

### Phase 0 — Scaffold
**Deliver:** repo structure above; `uv` project in `etl/`; Vite+React+TS app in
`app/` that builds and serves a blank shell; `CLAUDE.md`; CI stubs.
**Accept:** `uv run python -c "import httpx,duckdb"` works; `npm run build` in
`app/` succeeds; app serves a titled empty page.

### Phase 1 — ETL / snapshot (the foundation)
**Deliver:** `etl/` produces all artifacts in `data/`. `fetch.py` implements the
ECCO client (reuse the pattern from the existing `ecco_client.py`: discovery via
`/measures`, pull via `fips-value`, bulk via `download-all`). `catalog.py` builds
`catalog.parquet` with the composite `measure_id` rule and `is_numeric` flag.
`pivot.py` builds long + wide. `geo.py` emits TopoJSON keyed by FIPS. `verify.py`
runs acceptance checks.
**Accept:**
- `catalog.parquet` has every measure returned by `/stats/measures`, each with a non-null `unit` mapping to the enum.
- `county_wide.parquet` has 64 rows; `fips` is string with leading zeros preserved.
- Every `measure_id` in any `*_long` exists in `catalog`.
- Wide columns are exactly the numeric primary series.
- **`aac` spot-check**: pick one county + one SCP incidence measure, compare `value` and `aac` against the published State Cancer Profiles figure; record the finding in `verify.py` output and confirm/correct the `aac` interpretation note in §3.
- TopoJSON feature ids join 1:1 to `county_wide.fips`.

### Phase 2 — Data-loading harness
**Deliver:** `db/duckdb.ts` boots DuckDB-WASM, fetches the Parquet as ArrayBuffers,
registers them, exposes `query(sql): Promise<rows>`. `data/catalog.ts` loads the
catalog into a typed `Measure[]` with unit-aware formatters. App renders the
measure list grouped by category.
**Accept:** opening the app lists all measures from the catalog (no hardcoding);
a dev console `query("select count(*) from county_wide")` returns 64.

### Phase 3 — Choropleth view
**Deliver:** `ChoroplethView` + `MeasurePicker` + `FactorSelector` +
`ProvenancePanel`. Pick a measure (and factor values where present) → Vega-Lite
`geoshape` choropleth of Colorado counties, colored by `value`, with a legend,
tooltips (county name, value formatted by unit, `aac`), and a state-reference
annotation. Provenance panel shows `source` + linked `source_url`.
**Accept:** every numeric and categorical measure renders without error; switching
factors re-queries the long table and recolors; suppressed counties render in a
distinct "no data" color; number formatting matches the unit.

### Phase 4 — Correlation + scatter
**Deliver:** `CorrelationView` with multi-select of numeric measures → correlation
heatmap (Pearson default, Spearman toggle), pairwise-complete. Clicking a cell opens
`ScatterView`: the two measures across counties, points labeled, optional regression
line, county hover. Correlation computed in DuckDB SQL or `lib/stats.ts`.
**Accept:** heatmap is symmetric with diagonal = 1; measures with insufficient
overlap are flagged rather than producing NaN cells; scatter axes use unit-aware
formatting and identify each county.

### Phase 5 — Grouping / clustering
**Deliver:** `GroupingView` with two modes: (a) **cluster** — select numeric
measures, standardize, run k-means (k selectable) or hierarchical clustering, color
the county map by cluster and show per-cluster measure profiles (grouped bars); (b)
**quantile** — pick one measure, bin counties into n quantiles, compare a chosen
cancer outcome across bins (grouped/box plot). Handles missing values via
listwise drop or mean-impute (user-toggleable, default drop).
**Accept:** cluster assignments are stable for a fixed seed; the map and the
profile plot share the same cluster coloring; quantile mode correctly orders bins
and shows the outcome contrast.

### Phase 6 — Cross-view linking & polish
**Deliver:** shared selection in `state/store.ts` — selecting/hovering a county in
any view highlights it everywhere; URL-encoded app state (active view, measures,
factors, group settings) for shareable links; global provenance/about panel;
CSV/PNG export of the active view; responsive layout.
**Accept:** a shared URL restores the exact view; brushing a county propagates
across all open views; exports produce correct files.

### Phase 7 — MCP server (optional, parallelizable after Phase 1)
**Deliver:** `mcp/server.py` (FastMCP) reading the same Parquet via DuckDB:
discovery tools over the catalog, a parameterized query tool over the wide/long
tables, county GeoJSON as a resource. No duplication of measure logic — it consumes
the Phase 1 artifacts.
**Accept:** an LLM client can list measures, fetch a measure's per-county values,
and retrieve county geometry, all without guessing measure names.

---

## 8. Conventions

- **FIPS are strings.** Everywhere. This is the single most common source of join bugs.
- The **catalog is authoritative** for labels, units, sources, and valid factor values. The UI must not hardcode measure names or assume factors.
- **Units drive behavior**: formatting, legend type, and whether a measure is eligible for correlation/clustering all derive from `unit` / `is_numeric`.
- **Always surface provenance.** These are public-health statistics; every view shows source + link.
- **The snapshot is reproducible and idempotent**: re-running `snapshot.py` against the same API state produces byte-stable Parquet (sort rows deterministically before writing).
- No secrets are required anywhere — the API is public. Do not add auth scaffolding.

---

## 9. Resolved questions

### `aac` meaning — resolved Phase 1.0 (2026-05-26)

`aac` is the **average annual count** accompanying the age-adjusted rate.
Verified by `etl/src/co_cancer_atlas_etl/aac_probe.py`, which compares the
ECCO response for Denver County (FIPS 08031), `scpincidence / "All Cancer
Sites"`, against the published State Cancer Profiles figure for the same
slice (2018–2022, all races, both sexes, all ages, all stages):

| field | ECCO | SCP | delta |
|---|---:|---:|---:|
| value (rate / 100k) | 404.1 | 400.3 | 0.9% |
| aac (annual count)  | 2746.0 | 2765.0 | 0.7% |

The ≈1% delta is attributable to year-range differences between snapshots.
The probe will be re-run as part of `verify.py` (Phase 1.5) to catch drift
on future snapshot refreshes.
