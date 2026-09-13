"""Persist validated canonical records via SQLAlchemy repositories."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from crypto_lab.data.models import DataQualityEvent, MarketData, MarketQuote, MarketTrade
from crypto_lab.data.records import CanonicalCandle, CanonicalQuote, CanonicalTrade, MarketRecord
from crypto_lab.data.repositories import (
    DataQualityEventRepository,
    MarketDataRepository,
    MarketQuoteRepository,
    MarketTradeRepository,
)
from crypto_lab.data.validator import ValidationIssue


class MarketDataStorage:
    """STORE stage of the pipeline."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.candles = MarketDataRepository(session)
        self.trades = MarketTradeRepository(session)
        self.quotes = MarketQuoteRepository(session)
        self.quality = DataQualityEventRepository(session)

    def store(self, record: MarketRecord) -> MarketData | MarketTrade | MarketQuote:
        if isinstance(record, CanonicalCandle):
            return self.store_candle(record)
        if isinstance(record, CanonicalTrade):
            return self.store_trade(record)
        if isinstance(record, CanonicalQuote):
            return self.store_quote(record)
        raise TypeError(f"Unsupported record type: {type(record)}")

    def store_candle(self, c: CanonicalCandle) -> MarketData:
        entity = MarketData(
            symbol=c.symbol,
            timeframe=c.timeframe,
            ts=c.event_time,
            open=c.open,
            high=c.high,
            low=c.low,
            close=c.close,
            volume=c.volume,
            source=c.source,
            event_time=c.event_time,
            received_at=c.received_at,
            source_symbol=c.source_symbol or c.symbol,
            base_asset=c.base_asset,
            quote_asset=c.quote_asset,
            canonical_asset=c.canonical_asset,
        )
        return self.candles.upsert_candle(entity)

    def store_trade(self, t: CanonicalTrade) -> MarketTrade:
        entity = MarketTrade(
            source=t.source,
            symbol=t.symbol,
            event_time=t.event_time,
            received_at=t.received_at,
            price=t.price,
            quantity=t.quantity,
            trade_id=t.trade_id,
            side=t.side,
            source_symbol=t.source_symbol or t.symbol,
            base_asset=t.base_asset,
            quote_asset=t.quote_asset,
            canonical_asset=t.canonical_asset,
        )
        return self.trades.upsert_trade(entity)

    def store_quote(self, q: CanonicalQuote) -> MarketQuote:
        entity = MarketQuote(
            source=q.source,
            symbol=q.symbol,
            event_time=q.event_time,
            received_at=q.received_at,
            bid=q.bid,
            ask=q.ask,
            spread=q.spread,
            last=q.last,
            volume_24h=q.volume_24h,
            source_symbol=q.source_symbol or q.symbol,
            base_asset=q.base_asset,
            quote_asset=q.quote_asset,
            canonical_asset=q.canonical_asset,
        )
        return self.quotes.upsert_quote(entity)

    def log_quality_issue(
        self,
        issue: ValidationIssue,
        *,
        record: MarketRecord | None = None,
        record_type: str = "unknown",
    ) -> DataQualityEvent:
        source = symbol = None
        event_time = received_at = None
        if record is not None:
            source = record.source
            symbol = record.symbol
            event_time = record.event_time
            received_at = record.received_at
            record_type = type(record).__name__
        evt = DataQualityEvent(
            source=source,
            symbol=symbol,
            record_type=record_type,
            rule=issue.rule,
            severity=issue.severity,
            message=issue.message,
            payload_json=json.dumps(issue.details, default=str),
            event_time=event_time,
            received_at=received_at,
        )
        return self.quality.add(evt)
