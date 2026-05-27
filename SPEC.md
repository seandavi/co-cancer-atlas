# Colorado Cancer Atlas — Build Specification

A **chat-first exploration interface** over Colorado cancer risk, incidence,
mortality, and the social/environmental determinants around them. Users talk to
the application in natural language; an LLM (Gemini via the Vercel AI SDK)
plans, calls structured tools, and the UI renders the tool outputs inline:
choropleths, scatter plots, correlation matrices, tables, and short written
analyses. All data comes from the public **ECCO API** (`https://api.coe-ecco.org`);
the runtime reads a precomputed **Parquet + TopoJSON** snapshot via **DuckDB-WASM**
running inside a Cloudflare Worker.

This document is the contract. It is written so the work can proceed in
discrete, independently verifiable phases. Build phases in order; each has
explicit deliverables and acceptance criteria.

---

## 1. Goals & non-goals

**Goals**
- Chat is the primary interface. Users describe what they want to see ("show
  me a map of lung cancer incidence among Black women") and the LLM picks the
  right measure, factor combination, and viz.
- The LLM has structured tools — `list_measures`, `describe_measure`,
  `query_data` (SQL escape hatch), `render_choropleth`, `render_scatter`,
  `compute_correlation`, `cluster_counties`, `compose_report` — each returning
  data + a Vega-Lite spec the chat UI renders inline.
- Every claim the LLM makes is grounded in the catalog and the snapshot:
  provenance (`source`, `source_url`, snapshot timestamp) surfaces with every
  rendered artifact.
- Same Phase 1 snapshot powers everything. The ETL stays exactly as built.
- Deployment is a single Cloudflare Worker (via `@opennextjs/cloudflare`),
  with the Parquet snapshot bundled as static assets and read by DuckDB-WASM
  inside the Worker isolate.

**Non-goals (v1)**
- No live per-interaction calls to the ECCO API from the runtime — the
  snapshot is the source of truth.
- No persistent user accounts or per-user state on the server (sessions are
  cookie-scoped and ephemeral).
- No MCP server. The chat surface is the only consumer.
- No fine-tuning. Prompt + tools + the catalog are what shapes behavior.

---

## 2. Architecture

```
ECCO API ──▶ [1] ETL / snapshot (Python, offline)
                     │  pull → pivot → Parquet + catalog + TopoJSON
                     ▼
              [2] Static assets bundled with the Worker
                     │  county/tract long + wide matrices, catalog, geometry
                     ▼
              [3] Cloudflare Worker (Next.js via @opennextjs/cloudflare)
                     │  • DuckDB-WASM (server-side, isolate-warm)
                     │  • Tool layer: SQL/viz/cluster/report
                     │  • AI SDK chat route (Gemini, streamText + tools)
                     │  • React UI with Vega-Lite/Mermaid renderers
                     ▼
                  User chat
```

The pivotal design fact: **this is small data.** Colorado is 64 counties and
~1,250 tracts; total measures number in the low hundreds. The total snapshot
fits under 3 MB. Loading the Parquet into DuckDB-WASM is fast enough to run
once per warm Worker isolate; queries finish in single-digit ms.

---

## 3. Data sources

### 3.1 Cancer incidence & mortality — State Cancer Profiles (NCI)

Sourced via the monthly release of [`seandavi/state-cancer-profile-scraper`][scp],
which scrapes the NCI [State Cancer Profiles][scp-site] site and publishes
gzipped CSVs on GitHub Releases. The ETL reads the release CSV in place via
DuckDB httpfs — no local download — filters to Colorado counties
(`state_fips='08'`) plus the US national rollup (FIPS `00000`), and emits one
long-table row per (cancer × age × sex × race × stage × fips). Per-row the
SCP CSV carries strictly more than ECCO did:

- 95% CI on the age-adjusted rate (`lower_ci_rate`, `upper_ci_rate`)
- Recent 5-year trend direction, slope, and CI on the slope — inline on
  the same row, eliminating the prior `scpincidencetrend.*` /
  `scpdeathstrend.*` join pattern
- Rural / urban classifier from the 2023 USDA continuum codes
- Percent of cases diagnosed at late stage (incidence only)
- A national row per (cancer × factor combo) at FIPS `00000`, giving every
  measure a built-in "compare to US" anchor

State-level rollups (FIPS `XX000` for every state) are added to the
long table from the same release as soon as it carries `areatype='state'`
rows; the ETL filter (see `_read_release_csv` in `scp.py`) keeps:

- All Colorado county rows.
- Every state-level rollup (Colorado + 49 others + DC) — these light up
  the chat tool's "compare to other states" pattern.
- The standalone US national rollup at FIPS `00000`.

Until [scraper PR #10][scraper-pr]'s next release ships, no state rows
exist in the source CSV; the filter is forward-compatible (yields zero
state rows on older releases) and starts pulling them automatically
once `DEFAULT_SCP_RELEASE` is bumped to a post-PR-#10 tag.

The SCP release tag is pinned in `etl/src/co_cancer_atlas_etl/scp.py`
(`DEFAULT_SCP_RELEASE`) so snapshots are reproducible; bump alongside
each ETL refresh.

[scp]: https://github.com/seandavi/state-cancer-profile-scraper
[scp-site]: https://statecancerprofiles.cancer.gov/
[scraper-pr]: https://github.com/seandavi/state-cancer-profile-scraper/pull/10

### 3.2 Everything else — the ECCO API

GET-only, unauthenticated. Interactive docs at `/docs`. Relevant endpoints:

**Geography**
- `GET /counties` — county metadata + GeoJSON geometry (in `wkb_geometry` —
  despite the name, the API returns GeoJSON, not WKB). Keys include
  `us_fips` (the 5-digit FIPS used everywhere downstream).
- `GET /tracts` — census tracts; FIPS in the `fips` field.
- `GET /healthregions` — CDPHE health statistics regions.

**Stats** — each dataset exposes a uniform set of routes:
- `GET /stats/{level}/{dataset}/measures` → measure names.
- `GET /stats/{level}/{dataset}` → paginated raw rows.
- `GET /stats/{level}/{dataset}/fips-value?measure=…&filters=…` →
  pivot-friendly: `{min, max, state, state_source, unit, source, source_url,
  values: {FIPS: {value, aac}}}`.
- `GET /stats/{level}/{dataset}/as-csv?measure=…` → CSV.

**Catalog / convenience**
- `GET /stats/measures` → master catalog: every measure with `label`, `unit`,
  `source`, `source_url`, and `factors` (allowed values + defaults).
- `GET /stats/by-county/{fips}` → everything for one county, nested by category.
- `GET /stats/download-all` → zip of every stats table as CSV (~16 MB).
  Available but not used by the snapshot: the zip pre-sanitizes labels to
  filesystem-safe filenames, adding a multi-rule reverse mapping for no real
  gain over `fips-value`.

### API gotchas (must be respected in ETL)
- **FIPS are strings with leading zeros.** Never parse to int anywhere — not
  in Python, not in DuckDB, not in JS.
- **`fips-value` is the canonical pull.** One call per (level, dataset,
  measure, factor-combo); the catalog enumerates the combos via each
  measure's `factors.values`. Empty responses are dropped. Response carries
  `value`, `aac`, and per-measure `state` reference — single uniform pass.
- **`aac` companion field**: confirmed as the **average annual count**
  accompanying the age-adjusted rate. Verified Phase 1.0 against State Cancer
  Profiles for Denver County / All Cancer Sites incidence (ECCO 404.1 rate /
  2746 aac vs SCP 400.3 rate / 2765 aac — within 1%, attributable to
  year-range differences).
- **Factors**: cancer measures expand into many combinations (sex × stage ×
  race × age). Catalog defines defaults; non-default combos get a suffix on
  `measure_id`.
- **Suppressed / missing values**: expect nulls. Trend measures return
  categorical strings (`"stable"`, `"increasing"`) — carried in `value_str`.
- **Units gate visualizations.** `MeasureUnit` ∈ {percent, count, rate,
  dollar_amount, rank, ordinal, categorical, least_most}. Only continuous
  numeric units (percent, rate, count, dollar_amount) are valid for Pearson
  correlation and clustering.
- **Be polite**: cap ETL concurrency (≤8), exponential backoff with jitter.
  The snapshot runs rarely (weekly cron).

---

## 4. Data contract (Parquet schemas)

These schemas are the agreement between `etl/` and the chat tools.

### `catalog.parquet`
One row per `measure_id`. Drives every tool that names measures, formats
values, or gates a viz by `is_numeric`.

| column | type | notes |
|---|---|---|
| `measure_id` | string | stable key; composite below |
| `dataset` | string | e.g. `scpincidence` |
| `dataset_label` | string | human dataset label (e.g. "Cancer Incidence (age-adj per 100k)") |
| `level` | string | `county` \| `tract` \| `healthregion` |
| `category` | string | grouping label (= `dataset_label` today) |
| `measure` | string | catalog measure key |
| `label` | string | human measure label |
| `unit` | string | the `MeasureUnit` enum value |
| `source` | string | publisher (may be empty for SCP datasets — fall back per-dataset) |
| `source_url` | string | |
| `state_value` | double | nullable; state-level reference for the primary series |
| `factors` | string (JSON) | `{factor: {label, default, values:{code:label}}}` |
| `is_numeric` | bool | true when unit ∈ {percent, rate, count, dollar_amount} |

**Composite `measure_id` rule:** for factorless measures or the primary
series (all factors at their default), `measure_id = "{dataset}.{measure}"`.
Non-default combinations append a sorted suffix:
`"{dataset}.{measure}#race=Black NH;sex=Female"`.

### `{level}_long.parquet`  (`county_long`, `tract_long`)
Tidy long; complete record across all factor combinations the source has data for.

| column | type | notes |
|---|---|---|
| `fips` | string | region key (5-digit county, 11-digit tract). Cancer rows additionally carry FIPS `00000` (US national) and, when source release supports it, `XX000` for every US state — including `08000` for Colorado. |
| `measure_id` | string | FK to catalog (includes factor suffix where applicable) |
| `value` | double | nullable; populated when measure is numeric |
| `value_str` | string | nullable; populated when measure is non-numeric (ordinal/rank/etc.) |
| `aac` | double | nullable; average annual count |
| `value_lo` | double | nullable; 95% CI lower bound on rate (SCP cancer rows only) |
| `value_hi` | double | nullable; 95% CI upper bound on rate (SCP cancer rows only) |
| `trend_str` | string | nullable; recent trend direction — one of `stable`, `rising`, `falling` (SCP cancer rows only) |
| `trend_pct` | double | nullable; recent 5-year trend in rate, %/year (SCP cancer rows only) |
| `trend_pct_lo` | double | nullable; 95% CI lower bound on trend slope (SCP cancer rows only) |
| `trend_pct_hi` | double | nullable; 95% CI upper bound on trend slope (SCP cancer rows only) |
| `rural_urban` | string | nullable; 2023 USDA rural/urban classifier — `Urban` or `Rural` (SCP cancer rows only) |
| `pct_late_stage` | double | nullable; % of cases diagnosed at late stage (SCP incidence rows only) |

The cancer-specific extension columns (`value_lo` … `pct_late_stage`) are
all null on non-cancer rows. They populate only for primary measures from
`scpincidence` and `scpdeaths` (plus their factor combinations).

### `{level}_wide.parquet`  (`county_wide`, `tract_wide`)
Pivoted primary-series matrix for correlation/clustering. One row per region.

| column | type | notes |
|---|---|---|
| `fips` | string | region key |
| `name` | string | region name (county name for county; FIPS for tract) |
| `<measure_id>` | double | one column per primary, numeric `measure_id`; nullable |

Only `is_numeric` primary series become columns. Column names are the
`measure_id` strings verbatim (quote them in SQL).

### Geometry
- `co_counties.topojson`, `co_tracts.topojson` — TopoJSON of Colorado
  counties / tracts. Feature `id` is the FIPS string matching `fips`
  in the long/wide tables.
- `us_states.topojson` — TopoJSON of all 50 states + DC + territories,
  from [us-atlas][us-atlas] at 10 m resolution (~115 KB). Feature `id`
  is the 2-digit state FIPS string (e.g. `'08'` for Colorado). For
  joins to state-level cancer rows (FIPS `XX000`), transform with
  `LEFT(fips, 2)` on the long-table side.

[us-atlas]: https://github.com/topojson/us-atlas

### Hosting
Total snapshot is ~2.5 MB. Bundled as Worker static assets and read via the
asset binding from inside the Worker. If artifacts grow past a few MB,
relocate to R2 (already configured) and fetch with range requests.

---

## 5. Tech stack

**ETL** — Python 3.12+, managed with `uv`. Deps: `httpx` (async), `polars`,
`duckdb`, `pyarrow`, `topojson`, `tenacity` (retry/backoff).

**Runtime** — Cloudflare Worker built from a Next.js 16 (App Router) project
via `@opennextjs/cloudflare`. Core deps:
- `@ai-sdk/google`, `@ai-sdk/react`, `ai` — Vercel AI SDK + Gemini.
- `@duckdb/duckdb-wasm` — DuckDB inside the Worker isolate.
- `vega`, `vega-lite`, `vega-embed` — viz; the LLM emits Vega-Lite specs.
- `mermaid` — for the `compose_report` tool's diagrams.
- `react-markdown`, `remark-gfm` — assistant prose rendering.
- `tailwindcss@4` — styling.

Pin exact versions in lockfiles; the AI SDK in particular moves fast.

**Deploy** — Cloudflare Workers via `wrangler deploy`. Two GitHub Actions:
- `etl-refresh.yml` — weekly cron + manual; runs `snapshot.py`, opens a
  PR with the data diff for human review.
- `deploy.yml` — push to `main` → `wrangler deploy`.

Secrets (Worker): `GOOGLE_GENERATIVE_AI_API_KEY`. Optional: R2 binding if
snapshot moves off-bundle.

---

## 6. Repository layout

```
co-cancer-atlas/
  README.md
  SPEC.md                    # this document
  CLAUDE.md                  # conventions + commands for Claude Code
  etl/                       # unchanged from Phase 1
    pyproject.toml
    src/co_cancer_atlas_etl/{client,fetch,catalog,pivot,geo,verify,snapshot,aac_probe}.py
    tests/
  data/                      # generated artifacts (committed; ~2.5 MB)
    catalog.parquet
    {county,tract}_{long,wide}.parquet
    co_{counties,tracts}.topojson
  app/                       # Next.js 16 + AI SDK + DuckDB-WASM
    package.json
    next.config.ts
    wrangler.jsonc           # Cloudflare Worker config (Static Assets binding)
    open-next.config.ts
    app/
      layout.tsx
      page.tsx               # chat shell
      api/
        chat/route.ts        # AI SDK streamText + tools
      components/
        Chat.tsx
        Message.tsx
        ChatInput.tsx
        ToolResult.tsx
        VegaChart.tsx
        Mermaid.tsx
      lib/
        db.ts                # DuckDB-WASM boot + queryAll(sql)
        catalog.ts           # typed Measure model + measureIdFor + formatters
        tools/
          index.ts           # tool registry
          listMeasures.ts
          describeMeasure.ts
          queryData.ts
          renderChoropleth.ts
          renderScatter.ts
          computeCorrelation.ts
          clusterCounties.ts
          composeReport.ts
        prompts/
          system.ts
  .github/workflows/
    etl-refresh.yml
    deploy.yml
    test.yml
```

---

## 7. Build components (phased)

Each phase is a self-contained component. Do not start a phase until the prior
phase's acceptance criteria pass. Phases 0 and 1 are **complete**; Phases 2-7
are the chat-first work.

### Phase 0 — Scaffold ✅ done
Repo, ETL uv project, CI stubs.

### Phase 1 — ETL / snapshot ✅ done
Async ECCO client, catalog, pivot via fips-value, geo, verify, snapshot
orchestrator. Initial data committed under `data/`.

### Phase 2 — Next.js + Worker scaffolding
**Deliver:** `app/` is a Next.js 16 project (App Router); `@opennextjs/cloudflare`
adapter wired; `wrangler.jsonc` declares a Static Assets binding for `../data/`;
`tsconfig`, ESLint, Tailwind 4 configured; a blank chat shell renders.
**Accept:** `npm run dev` serves a styled empty chat at `/`; `npm run build`
followed by `wrangler dev` runs the same page out of a built Worker; `wrangler
deploy --dry-run` succeeds.

### Phase 3 — DuckDB-WASM + chat shell
**Deliver:** `lib/db.ts` boots DuckDB-WASM in the Worker, registers the
snapshot Parquet via the asset binding, and exposes `queryAll(sql)`. A health
check endpoint returns `{ counties: 64, measures: 288, snapshot_at: ... }`.
The `/api/chat` route uses AI SDK + Gemini with an empty tools registry; the
client streams text replies.
**Accept:** chatting with the bare model works end-to-end (text in, streamed
text out); GET `/api/health` returns the expected counts; cold-start budget
documented.

### Phase 4 — Tool layer (data tools)
**Deliver:** Three foundational tools — `list_measures` (search + filter the
catalog), `describe_measure` (full metadata for an id), `query_data` (SELECT
only against the snapshot, capped row count). System prompt teaches the model
to discover before querying.
**Accept:** "How many counties have data for prostate cancer incidence?"
produces a turn that calls `list_measures` → `query_data` → answer with a
numeric and source line.

### Phase 5 — Visualization tools
**Deliver:** `render_choropleth` (measure_id + factor values → Vega-Lite spec
with the right TopoJSON), `render_scatter` (two measure_ids → scatter with
optional regression), `compute_correlation` (numeric measures subset →
correlation matrix as a Vega-Lite heatmap). UI `ToolResult` component knows
how to render each. Suppressed regions get the "no data" color.
**Accept:** "Show me a map of breast cancer incidence in Black women" yields
a choropleth keyed to the right `measure_id` with provenance footer.

### Phase 6 — Clustering and reports
**Deliver:** `cluster_counties` (k-means / hierarchical over a chosen numeric
measure set, returns assignments + a Vega-Lite vis), `compose_report`
(structured multi-section markdown + embedded Vega-Lite specs + a Mermaid
diagram for measure relationships). Export as PDF (via pdfmake) or docx
(optional).
**Accept:** "Group counties by their cancer screening profile and tell me
what stands out" produces a clustering visualization plus a 3-section
written analysis.

### Phase 7 — Polish
**Deliver:** snapshot freshness display, URL-encoded session state for
shareable links, basic telemetry (turn count, tool-call mix), responsive
layout, accessibility pass.
**Accept:** shareable URLs round-trip a conversation; the freshness footer
shows snapshot date; ≥320 px works.

---

## 8. Conventions

- **FIPS are strings.** Everywhere.
- The **catalog is authoritative** for labels, units, sources, factor values.
  Tools must consult it; never hardcode measure names.
- **Units drive behavior**: formatting, legend type, eligibility for
  correlation/clustering all derive from `unit` / `is_numeric`.
- **Always surface provenance.** Every rendered artifact includes source +
  link and the snapshot timestamp.
- **The snapshot is reproducible and idempotent.**
- **No secrets in the bundle.** The Gemini key is a Worker secret only; the
  ECCO API is public and need not be reached at runtime.
- **Tool outputs are typed.** Each tool returns a stable shape the UI knows
  how to render — don't smuggle prose into tool outputs.

---

## 9. Resolved questions

### `aac` meaning — resolved Phase 1.0 (2026-05-26)

`aac` is the **average annual count** accompanying the age-adjusted rate.
Verified by `etl/src/co_cancer_atlas_etl/aac_probe.py` against State Cancer
Profiles for Denver County / All Cancer Sites incidence:

| field | ECCO | SCP | delta |
|---|---:|---:|---:|
| value (rate / 100k) | 404.1 | 400.3 | 0.9% |
| aac (annual count)  | 2746.0 | 2765.0 | 0.7% |

The ≈1% delta was attributable to year-range differences between snapshots.

### SCP migration — resolved Phase 1.6 (2026-05-26)

Cancer datasets (`scpincidence.*`, `scpdeaths.*`) no longer come from
ECCO; they are sourced directly from the State Cancer Profiles scraper
release (see §3.1). The ECCO `scpincidencetrend.*` / `scpdeathstrend.*`
datasets are dropped entirely — trend lives inline on the rate row now.
The aac probe was retargeted from "ECCO vs SCP" (no longer meaningful,
same source) to "snapshot vs published SCP figure" (catches stale
release pins and schema regressions). Tolerance tightened from 5% to
1% accordingly.
