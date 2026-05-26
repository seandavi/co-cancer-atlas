# etl/ — Colorado Cancer Atlas snapshot

Offline ETL that pulls the public ECCO API
(`https://api.coe-ecco.org`) into a deterministic Parquet + TopoJSON
snapshot under `../data/`. The browser-side SPA reads the snapshot
via DuckDB-WASM; this directory never runs in production.

See `../SPEC.md` §3 (API), §4 (Parquet schemas), and §7 Phase 1 for
the contract and acceptance criteria.

## Commands

```bash
uv sync
uv run python -c "import httpx, duckdb"   # phase 0 smoke test

# Phase 1 (not yet implemented):
uv run python -m co_cancer_atlas_etl.snapshot   # full snapshot → ../data/
uv run python -m co_cancer_atlas_etl.verify     # acceptance checks
```

## Layout

```
etl/
  pyproject.toml
  config.toml                  # base url, datasets, levels, concurrency  (phase 1)
  src/co_cancer_atlas_etl/
    __init__.py
    client.py                  # ECCO API client (ported from prototype)
    fetch.py                   # discovery + fips-value pulls           (phase 1)
    catalog.py                 # /stats/measures → catalog.parquet      (phase 1)
    pivot.py                   # long → wide via DuckDB                 (phase 1)
    geo.py                     # GeoJSON → TopoJSON (FIPS as feature id) (phase 1)
    verify.py                  # acceptance checks (incl. aac probe)    (phase 1)
    snapshot.py                # orchestrator                           (phase 1)
  tests/
```

## Rules (the ones easy to get wrong)

- FIPS codes are strings with leading zeros. Never int-parse them.
- The catalog is the single source of truth for labels, units, sources.
- Snapshots are byte-stable: sort rows deterministically before writing.
- Be polite to the API: concurrency ≤ 4, retry with backoff.
