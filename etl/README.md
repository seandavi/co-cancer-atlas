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

# Full snapshot (catalog + values + geometry) → ../data/
uv run snapshot
uv run snapshot --data-dir /tmp/test --concurrency 4

# Acceptance checks against ../data/
uv run verify

# Re-run the standalone SCP comparison (SPEC §9 aac question)
uv run aac-probe

# Tests + lint
uv run pytest
uv run ruff check .
```

## Layout

```
etl/
  pyproject.toml
  src/co_cancer_atlas_etl/
    __init__.py
    client.py                  # synchronous ECCO client (used by aac_probe)
    fetch.py                   # AsyncEccoClient: concurrency + retry
    catalog.py                 # /stats/measures → catalog.parquet
    pivot.py                   # fips-value → long + wide
    geo.py                     # /counties + /tracts → TopoJSON
    aac_probe.py               # SCP comparison (Phase 1.0)
    verify.py                  # SPEC §7 Phase 1 acceptance checks
    snapshot.py                # orchestrator
  tests/
```

## Rules (the ones easy to get wrong)

- FIPS codes are strings with leading zeros. Never int-parse them.
- The catalog is the single source of truth for labels, units, sources.
- Snapshots are byte-stable: sort rows deterministically before writing.
- Be polite to the API: concurrency ≤ 4, retry with backoff.
