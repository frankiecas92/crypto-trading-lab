"""HTTP client with retries, exponential backoff, and 429 handling (public data only)."""

from __future__ import annotations

import logging
import random
import time

from typing import Any, Mapping

import httpx

from crypto_lab.config.settings import Settings, get_settings
from crypto_lab.exceptions import ProviderError, RateLimitError

logger = logging.getLogger(__name__)


class ResilientHttpClient:
    """Thin wrapper around httpx with rate-limit awareness."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=self.settings.http_timeout_seconds,
            headers={"User-Agent": "crypto-trading-lab/0.2 (public-market-data)"},
        )
        self.last_used_weight: int | None = None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "ResilientHttpClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        last_exc: Exception | None = None
        retries = max(0, self.settings.http_max_retries)
        for attempt in range(retries + 1):
            try:
                resp = self._client.get(url, params=params, headers=headers)
                self._capture_weight(resp.headers)
                if resp.status_code == 429:
                    retry_after = _parse_retry_after(resp.headers)
                    if attempt >= retries:
                        raise RateLimitError(
                            f"429 Too Many Requests for {url}",
                            retry_after=retry_after,
                        )
                    sleep_s = retry_after or self._backoff(attempt)
                    logger.warning("Rate limited (429); sleeping %.2fs", sleep_s)
                    time.sleep(sleep_s)
                    continue
                if resp.status_code >= 500:
                    if attempt >= retries:
                        raise ProviderError(
                            f"Server error {resp.status_code} for {url}: {resp.text[:200]}"
                        )
                    time.sleep(self._backoff(attempt))
                    continue
                if resp.status_code >= 400:
                    raise ProviderError(
                        f"HTTP {resp.status_code} for {url}: {resp.text[:300]}"
                    )
                return resp.json()
            except (RateLimitError, ProviderError):
                raise
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt >= retries:
                    break
                time.sleep(self._backoff(attempt))
        raise ProviderError(f"Request failed for {url}: {last_exc}") from last_exc

    def _capture_weight(self, headers: Mapping[str, str]) -> None:
        # Binance uses X-MBX-USED-WEIGHT-1M (case-insensitive)
        for key, val in headers.items():
            if key.lower().startswith("x-mbx-used-weight"):
                try:
                    self.last_used_weight = int(val)
                except ValueError:
                    pass
                break

    def _backoff(self, attempt: int) -> float:
        base = self.settings.rest_backoff_base_seconds
        return base * (2**attempt) + random.uniform(0, 0.25)


def _parse_retry_after(headers: Mapping[str, str]) -> float | None:
    for k, v in headers.items():
        if k.lower() == "retry-after":
            try:
                return float(v)
            except ValueError:
                return None
    return None
