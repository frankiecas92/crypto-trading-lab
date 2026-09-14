"""Historical OHLCV downloader — public REST only, small safe defaults.

Reuses existing providers, http_client (retries / 429 / backoff), normalizer,
validator, storage, market_data, and parse_instrument. Does not mix venues.

Does NOT auto-download huge ranges. Resume skips already-stored timestamps.
Duplicates are not inserted. Gaps are never imputed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Sequence

from sqlalchemy.orm import Session

from crypto_lab.backtest.timeframes import TIMEFRAME_MS
from crypto_lab.config.settings import Settings, get_settings
from crypto_lab.data.historical.constants import (
    BINANCE_PAGE_LIMIT,
    COINBASE_PAGE_LIMIT,
    DEFAULT_MAX_BARS,
    DEFAULT_RANGE_DAYS,
    DEFAULT_TIMEFRAME,
    HARD_MAX_BARS,
    PAGE_SLEEP_SECONDS,
    SUPPORTED_SOURCES,
    SUPPORTED_TIMEFRAMES,
)
from crypto_lab.data.pipeline import DataPipeline
from crypto_lab.data.providers.base import DataProvider
from crypto_lab.data.providers.binance import BinanceSpotProvider
from crypto_lab.data.providers.coinbase import CoinbaseExchangeProvider
from crypto_lab.data.records import CanonicalCandle
from crypto_lab.data.repositories import MarketDataRepository
from crypto_lab.data.symbols import parse_instrument
from crypto_lab.exceptions import ProviderError, ValidationError

logger = logging.getLogger(__name__)


def provider_for_source(source: str, settings: Settings | None = None) -> DataProvider:
    """Venue-specific provider — never mix Binance USDT with Coinbase USD."""
    cfg = settings or get_settings()
    src = source.strip().lower()
    if src == "binance":
        return BinanceSpotProvider(cfg)
    if src == "coinbase":
        return CoinbaseExchangeProvider(cfg)
    raise ValidationError(f"Unsupported historical source {source!r}; use {SUPPORTED_SOURCES}")


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _tf_delta(timeframe: str) -> timedelta:
    key = timeframe.strip().lower()
    if key not in TIMEFRAME_MS:
        raise ValidationError(f"Unsupported timeframe {timeframe!r}")
    return timedelta(milliseconds=TIMEFRAME_MS[key])


def is_candle_closed(
    event_time: datetime,
    timeframe: str,
    now: datetime | None = None,
) -> bool:
    """True iff the candle that opened at ``event_time`` is fully closed by ``now``.

    A forming/incomplete bar has ``event_time + timeframe_delta > now_utc`` and
    must not be stored as historical OHLCV. Equality at the close instant counts
    as closed (the period has elapsed).
    """
    et = _aware(event_time)
    n = _aware(now or datetime.now(timezone.utc))
    return et + _tf_delta(timeframe) <= n


def default_window(
    *,
    timeframe: str = DEFAULT_TIMEFRAME,
    days: int = DEFAULT_RANGE_DAYS,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    end = _aware(now or datetime.now(timezone.utc))
    start = end - timedelta(days=days)
    return start, end


def fetch_klines_compat(
    provider: DataProvider,
    symbol: str,
    *,
    timeframe: str,
    limit: int,
    start: datetime | None,
    end: datetime | None,
) -> List[CanonicalCandle]:
    """Call fetch_klines with start/end when the provider supports them."""
    try:
        return provider.fetch_klines(
            symbol, timeframe=timeframe, limit=limit, start=start, end=end
        )
    except TypeError:
        return provider.fetch_klines(symbol, timeframe=timeframe, limit=limit)


def page_limit_for(source: str) -> int:
    src = source.strip().lower()
    if src == "coinbase":
        return COINBASE_PAGE_LIMIT
    return BINANCE_PAGE_LIMIT


@dataclass
class DownloadResult:
    source: str
    source_symbol: str
    timeframe: str
    start: datetime
    end: datetime
    fetched: int = 0
    stored: int = 0
    skipped_existing: int = 0
    skipped_open: int = 0
    rejected: int = 0
    pages: int = 0
    truncated: bool = False
    candles: List[CanonicalCandle] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "source_symbol": self.source_symbol,
            "timeframe": self.timeframe,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "fetched": self.fetched,
            "stored": self.stored,
            "skipped_existing": self.skipped_existing,
            "skipped_open": self.skipped_open,
            "rejected": self.rejected,
            "pages": self.pages,
            "truncated": self.truncated,
            "notes": list(self.notes),
        }


class HistoricalDownloader:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        provider: DataProvider | None = None,
        source: str = "binance",
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.source = source.strip().lower()
        self.provider = provider or provider_for_source(self.source, self.settings)
        self.repo = MarketDataRepository(session)
        self._owns_provider = provider is None

    def close(self) -> None:
        if self._owns_provider and hasattr(self.provider, "close"):
            self.provider.close()  # type: ignore[attr-defined]

    def download(
        self,
        symbol: str,
        *,
        timeframe: str = DEFAULT_TIMEFRAME,
        start: datetime | None = None,
        end: datetime | None = None,
        max_bars: int | None = None,
        resume: bool = True,
        now: datetime | None = None,
    ) -> DownloadResult:
        """Paginated public REST download with resume and duplicate skip.

        Refuses oversized requests. Default window is a few days of 1h.
        Open/incomplete candles (event_time + timeframe > now_utc) are skipped.
        """
        tf = timeframe.strip().lower()
        if tf not in SUPPORTED_TIMEFRAMES:
            raise ValidationError(f"Unsupported timeframe {timeframe!r}")
        src = self.source
        if src not in SUPPORTED_SOURCES:
            raise ValidationError(f"Unsupported source {src!r}")

        hard = int(getattr(self.settings, "historical_hard_max_bars", HARD_MAX_BARS))
        default_max = int(getattr(self.settings, "historical_max_bars", DEFAULT_MAX_BARS))
        cap = default_max if max_bars is None else int(max_bars)
        if cap <= 0:
            raise ValidationError("max_bars must be positive")
        if cap > hard:
            raise ValidationError(
                f"Refusing download max_bars={cap} > hard max {hard}. "
                "Phase 4A does not auto-download huge ranges."
            )

        inst = parse_instrument(src, symbol)
        if start is None or end is None:
            d_start, d_end = default_window(timeframe=tf)
            start = start or d_start
            end = end or d_end
        start = _aware(start)
        end = _aware(end)
        if end <= start:
            raise ValidationError("end must be after start")

        step = _tf_delta(tf)
        span_bars = int((end - start) / step) + 1
        truncated = False
        if span_bars > cap:
            end = start + step * (cap - 1)
            truncated = True
            logger.warning(
                "Truncating requested range to max_bars=%s (%s → %s)", cap, start, end
            )

        existing_ts: set[datetime] = set()
        if resume:
            rows = self.repo.list_candles_range(
                source=src,
                symbol=inst.source_symbol,
                timeframe=tf,
                start=start,
                end=end,
            )
            existing_ts = {r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc) for r in rows}

        result = DownloadResult(
            source=src,
            source_symbol=inst.source_symbol,
            timeframe=tf,
            start=start,
            end=end,
            skipped_existing=len(existing_ts),
            truncated=truncated,
        )
        now_utc = _aware(now or datetime.now(timezone.utc))
        if truncated:
            result.notes.append(f"range truncated to max_bars={cap}")
        result.notes.append(
            "Public market data only. Not EDGE_CONFIRMED / PROFITABLE / trading evidence."
        )
        result.notes.append(
            "Open/incomplete candles excluded: event_time + timeframe_delta > now_utc "
            "(forming bar is not stored as historical OHLCV)."
        )

        if resume and len(existing_ts) >= cap:
            result.notes.append("resume: already have max_bars in range; skip network")
            logger.info("Resume skip — %s candles already stored", len(existing_ts))
            return result

        page_size = min(page_limit_for(src), cap, 500)
        cursor = start
        collected: List[CanonicalCandle] = []
        seen: set[datetime] = set(existing_ts)
        target = cap

        while cursor <= end and len(seen) < target:
            page_end = min(end, cursor + step * (page_size - 1))
            logger.info(
                "Historical page %s %s %s [%s, %s] limit=%s",
                src,
                inst.source_symbol,
                tf,
                cursor.isoformat(),
                page_end.isoformat(),
                page_size,
            )
            try:
                batch = fetch_klines_compat(
                    self.provider,
                    inst.source_symbol,
                    timeframe=tf,
                    limit=page_size,
                    start=cursor,
                    end=page_end,
                )
            except ProviderError:
                raise
            result.pages += 1
            result.fetched += len(batch)
            if not batch:
                break
            batch_sorted = sorted(batch, key=lambda c: c.event_time)
            advanced = False
            for c in batch_sorted:
                et = c.event_time if c.event_time.tzinfo else c.event_time.replace(tzinfo=timezone.utc)
                if et < start or et > end:
                    continue
                # Venue identity must match requested source/symbol
                if c.source != src or c.symbol != inst.source_symbol:
                    result.notes.append(
                        f"skipped mismatched candle source={c.source} symbol={c.symbol}"
                    )
                    continue
                if not is_candle_closed(et, tf, now=now_utc):
                    result.skipped_open += 1
                    continue
                if et in seen:
                    result.skipped_existing += 1
                    continue
                seen.add(et)
                collected.append(c)
                advanced = True
                if len(seen) >= target:
                    break
            last_et = batch_sorted[-1].event_time
            nxt = last_et + step
            if nxt <= cursor:
                # avoid infinite loop if provider repeats the same page
                break
            cursor = nxt
            if not advanced and not batch:
                break
            if result.pages > 0:
                time.sleep(PAGE_SLEEP_SECONDS)

        if result.skipped_open:
            result.notes.append(
                f"Skipped {result.skipped_open} open/incomplete candle(s): "
                "event_time + timeframe > now_utc (forming bar not stored)."
            )

        if collected:
            pipe = DataPipeline(self.session, provider=self.provider, settings=self.settings)
            pipe.validator.reset_state()
            proc = pipe.process_many(collected)
            result.stored = proc.stored
            result.rejected = proc.rejected
            result.candles = list(proc.records_stored)
        else:
            result.notes.append("no new candles to store")

        logger.info(
            "Historical download done source=%s symbol=%s tf=%s stored=%s skipped=%s pages=%s",
            src,
            inst.source_symbol,
            tf,
            result.stored,
            result.skipped_existing,
            result.pages,
        )
        return result
