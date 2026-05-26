"""
ecco_client.py — a thin client for the ECCO Colorado cancer catchment API.

API: https://api.coe-ecco.org  (FastAPI, GET-only, no auth/key required)
Docs: https://api.coe-ecco.org/docs  (interactive Swagger UI)

Design note: the API is a geography x topic grid. Every "stats dataset"
(scpincidence, scpdeaths, rfandscreening, ...) exposes the same 4 routes:
    /stats/{level}/{dataset}/measures      -> list available measure names
    /stats/{level}/{dataset}               -> paginated raw rows
    /stats/{level}/{dataset}/fips-value    -> {FIPS: {value, aac}} for one measure
    /stats/{level}/{dataset}/as-csv        -> CSV download
So the reliable workflow is always: discover measures -> query fips-value.

Requires: httpx  (pip install httpx)   pandas optional for the as_dataframe helper.
"""

from __future__ import annotations

import io
from typing import Any

import httpx

BASE_URL = "https://api.coe-ecco.org"

# County-level datasets most relevant to cancer risk & outcomes.
# (Full set also includes sociodemographics, economy, environment,
#  housingtrans, disparities, ucccresponders.)
CANCER_DATASETS = [
    "scpincidence",          # State Cancer Profiles: incidence rates
    "scpdeaths",             # State Cancer Profiles: mortality rates
    "scpincidencetrend",     # incidence trend over time
    "scpdeathstrend",        # mortality trend over time
    "cancerdisparitiesindex",
    "rfandscreening",        # risk factors & screening (smoking, mammography, etc.)
    "hpv",                   # HPV vaccination (has a 'sex' factor)
    "radon",
    "uvexposure",
]


class EccoClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 30.0):
        self._http = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "EccoClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _get(self, path: str, **params: Any) -> Any:
        # drop None-valued params so we don't send empty query strings
        params = {k: v for k, v in params.items() if v is not None}
        r = self._http.get(path, params=params)
        r.raise_for_status()
        return r

    # --- geography -------------------------------------------------------

    def counties(self) -> list[dict]:
        """Metadata + GeoJSON geometry for all Colorado counties."""
        return self._get("/counties").json()

    def health_regions(self) -> list[dict]:
        return self._get("/healthregions").json()

    def county_fips_lookup(self) -> dict[str, str]:
        """Convenience: {county_name: fips_code}. Geometry stripped out."""
        return {c["county"]: c["cnty_fips"] for c in self.counties()}

    # --- catalog / discovery --------------------------------------------

    def catalog(self) -> dict[str, Any]:
        """Master catalog of every measure: label, unit, source, and the
        factors (e.g. RE, Sex, Age) each measure supports with valid values."""
        return self._get("/stats/measures").json()

    def by_county(self, county_fips: str) -> dict[str, Any]:
        """Everything known about one county, nested by category -> measure."""
        return self._get(f"/stats/by-county/{county_fips}").json()

    def measures(self, dataset: str, level: str = "county") -> list[str]:
        """List the distinct measure names available in a dataset."""
        return self._get(f"/stats/{level}/{dataset}/measures").json()

    # --- the workhorse ---------------------------------------------------

    def fips_value(
        self,
        dataset: str,
        measure: str,
        level: str = "county",
        filters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Per-region values for one measure.

        Returns {min, max, state, unit, source, source_url, order,
                 values: {FIPS: {value, aac}}}.

        `filters` like {"RE": "White NH", "Sex": "Female"} is serialized to the
        API's "RE:White NH;Sex:Female" format. Use catalog() to find which
        factors and values a given measure accepts.
        """
        filt = None
        if filters:
            filt = ";".join(f"{k}:{v}" for k, v in filters.items())
        return self._get(
            f"/stats/{level}/{dataset}/fips-value", measure=measure, filters=filt
        ).json()

    def rows(
        self,
        dataset: str,
        level: str = "county",
        measure: str | None = None,
        size: int = 100,
        max_pages: int | None = None,
    ) -> list[dict]:
        """Pull raw rows across all pages (size capped at 100 by the API)."""
        out: list[dict] = []
        page = 1
        while True:
            payload = self._get(
                f"/stats/{level}/{dataset}", measure=measure, page=page, size=size
            ).json()
            out.extend(payload.get("items", []))
            if page >= payload.get("pages", page) or (max_pages and page >= max_pages):
                break
            page += 1
        return out

    def as_csv_text(
        self, dataset: str, level: str = "county", measure: str | None = None
    ) -> str:
        return self._get(f"/stats/{level}/{dataset}/as-csv", measure=measure).text

    def as_dataframe(
        self, dataset: str, level: str = "county", measure: str | None = None
    ):
        """Requires pandas. Loads a dataset's CSV straight into a DataFrame."""
        import pandas as pd  # local import so pandas stays optional

        return pd.read_csv(io.StringIO(self.as_csv_text(dataset, level, measure)))


if __name__ == "__main__":
    with EccoClient() as ecco:
        # 1. What cancer-incidence measures exist?
        inc_measures = ecco.measures("scpincidence")
        print(f"scpincidence has {len(inc_measures)} measures, e.g.:")
        for m in inc_measures[:10]:
            print("   ", m)

        # 2. Pull one measure across all CO counties.
        if inc_measures:
            measure = inc_measures[0]
            resp = ecco.fips_value("scpincidence", measure)
            vals = resp.get("values", {})
            print(f"\n'{measure}' ({resp.get('unit')}) — source: {resp.get('source')}")
            print(f"  state value: {resp.get('state')}, "
                  f"range {resp.get('min')}–{resp.get('max')} across "
                  f"{len(vals)} counties")

        # 3. Resolve a county name -> FIPS, then dump its full profile.
        fips = ecco.county_fips_lookup().get("Denver")
        if fips:
            profile = ecco.by_county(fips)
            print(f"\nDenver ({fips}) categories: "
                  f"{list(profile.get('categories', {}).keys())}")
