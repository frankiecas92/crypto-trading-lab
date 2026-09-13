"""Normalize provider payloads into canonical records (venue-native symbols)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from crypto_lab.data.records import (
    CanonicalCandle,
    CanonicalQuote,
    CanonicalTrade,
    ms_to_datetime,
)
from crypto_lab.data.symbols import Instrument, parse_instrument


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _instrument(source: str, source_symbol: str) -> Instrument:
    return parse_instrument(source, source_symbol)


def normalize_binance_kline(
    row: list | tuple,
    *,
    symbol: str,
    timeframe: str,
    received_at: datetime,
    source: str = "binance",
) -> CanonicalCandle:
    """Binance kline REST row -> CanonicalCandle.

    Row: [open_time, o, h, l, c, volume, close_time, ...]
    """
    open_time_ms = int(row[0])
    event_time = ms_to_datetime(open_time_ms)
    inst = _instrument(source, symbol)
    return CanonicalCandle(
        source=source,
        symbol=inst.source_symbol,
        source_symbol=inst.source_symbol,
        base_asset=inst.base_asset,
        quote_asset=inst.quote_asset,
        canonical_asset=inst.canonical_asset,
        event_time=_aware(event_time),
        received_at=_aware(received_at),
        timeframe=timeframe,
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
        raw={"kline": list(row)[:8]},
    )


def normalize_binance_ws_kline(
    payload: Mapping[str, Any],
    *,
    received_at: datetime,
    source: str = "binance",
) -> CanonicalCandle:
    """Binance combined stream kline event."""
    data = payload.get("data", payload)
    k = data["k"]
    symbol = data.get("s") or k.get("s")
    timeframe = k.get("i", "1m")
    event_ms = int(k.get("t") or data.get("E") or 0)
    inst = _instrument(source, str(symbol))
    return CanonicalCandle(
        source=source,
        symbol=inst.source_symbol,
        source_symbol=inst.source_symbol,
        base_asset=inst.base_asset,
        quote_asset=inst.quote_asset,
        canonical_asset=inst.canonical_asset,
        event_time=_aware(ms_to_datetime(event_ms)),
        received_at=_aware(received_at),
        timeframe=str(timeframe),
        open=float(k["o"]),
        high=float(k["h"]),
        low=float(k["l"]),
        close=float(k["c"]),
        volume=float(k["v"]),
        raw=dict(k),
    )


def normalize_binance_trade(
    payload: Mapping[str, Any],
    *,
    received_at: datetime,
    source: str = "binance",
) -> CanonicalTrade:
    data = payload.get("data", payload)
    symbol = data.get("s") or payload.get("symbol")
    event_ms = int(data.get("T") or data.get("E") or data.get("time") or 0)
    price = float(data.get("p") or data.get("price"))
    qty = float(data.get("q") or data.get("qty") or data.get("quantity"))
    trade_id = data.get("a") or data.get("t") or data.get("id")
    is_buyer_maker = data.get("m")
    side = None
    if is_buyer_maker is True:
        side = "SELL"
    elif is_buyer_maker is False:
        side = "BUY"
    inst = _instrument(source, str(symbol))
    return CanonicalTrade(
        source=source,
        symbol=inst.source_symbol,
        source_symbol=inst.source_symbol,
        base_asset=inst.base_asset,
        quote_asset=inst.quote_asset,
        canonical_asset=inst.canonical_asset,
        event_time=_aware(ms_to_datetime(event_ms)),
        received_at=_aware(received_at),
        price=price,
        quantity=qty,
        trade_id=str(trade_id) if trade_id is not None else None,
        side=side,
        raw=dict(data),
    )


def normalize_binance_book_ticker(
    payload: Mapping[str, Any],
    *,
    received_at: datetime,
    source: str = "binance",
) -> CanonicalQuote:
    data = payload.get("data", payload)
    symbol = data.get("s") or data.get("symbol")
    event_ms = int(data.get("E") or data.get("T") or 0)
    if event_ms <= 0:
        # bookTicker REST may not include event time — use received_at
        event_time = _aware(received_at)
    else:
        event_time = _aware(ms_to_datetime(event_ms))
    bid = float(data["b"]) if "b" in data else float(data.get("bidPrice"))
    ask = float(data["a"]) if "a" in data else float(data.get("askPrice"))
    inst = _instrument(source, str(symbol))
    return CanonicalQuote(
        source=source,
        symbol=inst.source_symbol,
        source_symbol=inst.source_symbol,
        base_asset=inst.base_asset,
        quote_asset=inst.quote_asset,
        canonical_asset=inst.canonical_asset,
        event_time=event_time,
        received_at=_aware(received_at),
        bid=bid,
        ask=ask,
        last=None,
        raw=dict(data),
    )


def normalize_coinbase_candle(
    row: list | tuple,
    *,
    product_id: str,
    timeframe: str,
    received_at: datetime,
    source: str = "coinbase",
) -> CanonicalCandle:
    """Coinbase candle: [time, low, high, open, close, volume] (time in seconds)."""
    ts_sec = int(row[0])
    event_time = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
    low, high, open_, close, volume = (
        float(row[1]),
        float(row[2]),
        float(row[3]),
        float(row[4]),
        float(row[5]),
    )
    inst = _instrument(source, product_id)
    return CanonicalCandle(
        source=source,
        symbol=inst.source_symbol,
        source_symbol=inst.source_symbol,
        base_asset=inst.base_asset,
        quote_asset=inst.quote_asset,
        canonical_asset=inst.canonical_asset,
        event_time=_aware(event_time),
        received_at=_aware(received_at),
        timeframe=timeframe,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        raw={"candle": list(row)},
    )


def normalize_coinbase_trade(
    payload: Mapping[str, Any],
    *,
    received_at: datetime,
    source: str = "coinbase",
) -> CanonicalTrade:
    product = payload.get("product_id") or payload.get("symbol")
    time_s = payload.get("time") or payload.get("created_at")
    if isinstance(time_s, (int, float)):
        event_time = datetime.fromtimestamp(float(time_s), tz=timezone.utc)
    else:
        # ISO8601
        event_time = datetime.fromisoformat(str(time_s).replace("Z", "+00:00"))
    trade_id = payload.get("trade_id") or payload.get("id")
    side = payload.get("side")
    if side:
        side = str(side).upper()
    inst = _instrument(source, str(product))
    return CanonicalTrade(
        source=source,
        symbol=inst.source_symbol,
        source_symbol=inst.source_symbol,
        base_asset=inst.base_asset,
        quote_asset=inst.quote_asset,
        canonical_asset=inst.canonical_asset,
        event_time=_aware(event_time),
        received_at=_aware(received_at),
        price=float(payload["price"]),
        quantity=float(payload.get("size") or payload.get("quantity")),
        trade_id=str(trade_id) if trade_id is not None else None,
        side=side,
        raw=dict(payload),
    )


def normalize_coinbase_ticker(
    payload: Mapping[str, Any],
    *,
    received_at: datetime,
    source: str = "coinbase",
) -> CanonicalQuote:
    product = payload.get("product_id") or payload.get("symbol")
    time_s = payload.get("time")
    if time_s:
        event_time = datetime.fromisoformat(str(time_s).replace("Z", "+00:00"))
    else:
        event_time = received_at
    bid = payload.get("best_bid") or payload.get("bid")
    ask = payload.get("best_ask") or payload.get("ask")
    last = payload.get("price")
    vol = payload.get("volume_24h") or payload.get("volume")
    inst = _instrument(source, str(product))
    return CanonicalQuote(
        source=source,
        symbol=inst.source_symbol,
        source_symbol=inst.source_symbol,
        base_asset=inst.base_asset,
        quote_asset=inst.quote_asset,
        canonical_asset=inst.canonical_asset,
        event_time=_aware(event_time),
        received_at=_aware(received_at),
        bid=float(bid) if bid is not None else None,
        ask=float(ask) if ask is not None else None,
        last=float(last) if last is not None else None,
        volume_24h=float(vol) if vol is not None else None,
        raw=dict(payload),
    )
