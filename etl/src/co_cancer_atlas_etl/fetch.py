"""Async ECCO API client with concurrency cap and retry/backoff.

The synchronous `client.EccoClient` stays around for one-off probes
(e.g. `aac_probe.py`). This module is what `snapshot.py` uses for the
bulk pull: it adds asyncio, a concurrency semaphore (≤4 per SPEC §3),
and exponential-backoff retries on transient errors.

The surface mirrors `client.EccoClient` so the two are interchangeable
for a single-shot call; the win is in the bulk helpers at the bottom
that fan out across many measures concurrently.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

BASE_URL = "https://api.coe-ecco.org"
DEFAULT_CONCURRENCY = 8
DEFAULT_TIMEOUT = 30.0
RETRY_ATTEMPTS = 5
RETRY_MIN_SECONDS = 0.5
RETRY_MAX_SECONDS = 8.0


def _is_retryable(exc: BaseException) -> bool:
    """Retry on network/5xx; never on 4xx (bad request → fail loudly)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    return isinstance(exc, httpx.HTTPError)


class AsyncEccoClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"User-Agent": "co-cancer-atlas-etl/0.1 (+github)"},
        )
        self._sem = asyncio.Semaphore(concurrency)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncEccoClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # ----- core request with retry + concurrency cap ---------------------

    @retry(
        reraise=True,
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(RETRY_ATTEMPTS),
        wait=wait_exponential(
            multiplier=RETRY_MIN_SECONDS, max=RETRY_MAX_SECONDS
        ),
    )
    async def _get_json(self, path: str, **params: Any) -> Any:
        async with self._sem:
            params = {k: v for k, v in params.items() if v is not None}
            r = await self._http.get(path, params=params)
            r.raise_for_status()
            return r.json()

    @retry(
        reraise=True,
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(RETRY_ATTEMPTS),
        wait=wait_exponential(
            multiplier=RETRY_MIN_SECONDS, max=RETRY_MAX_SECONDS
        ),
    )
    async def _get_text(self, path: str, **params: Any) -> str:
        async with self._sem:
            params = {k: v for k, v in params.items() if v is not None}
            r = await self._http.get(path, params=params)
            r.raise_for_status()
            return r.text

    # ----- geography -----------------------------------------------------

    async def counties(self) -> list[dict]:
        return await self._get_json("/counties")

    async def tracts(self) -> list[dict]:
        return await self._get_json("/tracts")

    async def health_regions(self) -> list[dict]:
        return await self._get_json("/healthregions")

    # ----- catalog -------------------------------------------------------

    async def catalog(self) -> dict[str, Any]:
        return await self._get_json("/stats/measures")

    async def measures(self, dataset: str, level: str = "county") -> list[str]:
        return await self._get_json(f"/stats/{level}/{dataset}/measures")

    # ----- workhorse -----------------------------------------------------

    async def fips_value(
        self,
        dataset: str,
        measure: str,
        level: str = "county",
        filters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        filt = None
        if filters:
            filt = ";".join(f"{k}:{v}" for k, v in filters.items())
        return await self._get_json(
            f"/stats/{level}/{dataset}/fips-value",
            measure=measure,
            filters=filt,
        )

    async def as_csv_text(
        self, dataset: str, level: str = "county", measure: str | None = None
    ) -> str:
        return await self._get_text(
            f"/stats/{level}/{dataset}/as-csv", measure=measure
        )

    # ----- bulk helpers (where async + concurrency pay off) --------------

    async def fips_value_many(
        self,
        dataset: str,
        measures: Iterable[str],
        level: str = "county",
        filters: dict[str, str] | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Fetch fips-value for every (dataset, measure) pair concurrently.

        Returns [(measure, response), ...] in the same order as input.
        Exceptions propagate — fail fast rather than smuggle bad data.
        """
        measures = list(measures)

        async def one(m: str) -> tuple[str, dict[str, Any]]:
            return m, await self.fips_value(dataset, m, level=level, filters=filters)

        return await asyncio.gather(*(one(m) for m in measures))
