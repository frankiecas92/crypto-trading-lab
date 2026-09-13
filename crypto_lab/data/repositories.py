"""Repository / DAO layer — Phase 1 CRUD + Phase 2 market data persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, Optional, Sequence, Type, TypeVar

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from crypto_lab.data.models import (
    Base,
    DataQualityEvent,
    MarketData,
    MarketQuote,
    MarketTrade,
    Portfolio,
    Position,
    Signal,
    StrategyVersion,
    SystemEvent,
    Trade,
)

T = TypeVar("T", bound=Base)


class Repository(Generic[T]):
    """Generic thin CRUD stub."""

    def __init__(self, session: Session, model: Type[T]) -> None:
        self.session = session
        self.model = model

    def add(self, entity: T) -> T:
        self.session.add(entity)
        self.session.flush()
        return entity

    def get(self, entity_id: int) -> Optional[T]:
        return self.session.get(self.model, entity_id)

    def list(self, limit: int = 100, offset: int = 0) -> Sequence[T]:
        stmt = select(self.model).offset(offset).limit(limit)
        return list(self.session.scalars(stmt).all())

    def delete(self, entity: T) -> None:
        self.session.delete(entity)
        self.session.flush()


class MarketDataRepository(Repository[MarketData]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, MarketData)

    def find_candle(
        self,
        *,
        source: str,
        symbol: str,
        ts: datetime,
        timeframe: str,
    ) -> Optional[MarketData]:
        stmt = select(MarketData).where(
            and_(
                MarketData.source == source,
                MarketData.symbol == symbol,
                MarketData.ts == ts,
                MarketData.timeframe == timeframe,
            )
        )
        return self.session.scalars(stmt).first()

    def upsert_candle(self, entity: MarketData) -> MarketData:
        """Insert candle; skip if duplicate (source, symbol, ts, timeframe)."""
        existing = self.find_candle(
            source=entity.source,
            symbol=entity.symbol,
            ts=entity.ts,
            timeframe=entity.timeframe,
        )
        if existing is not None:
            return existing
        return self.add(entity)

    def list_candles(
        self,
        *,
        symbol: str,
        timeframe: str,
        source: str | None = None,
        limit: int = 500,
    ) -> Sequence[MarketData]:
        clauses = [MarketData.symbol == symbol, MarketData.timeframe == timeframe]
        if source:
            clauses.append(MarketData.source == source)
        stmt = (
            select(MarketData)
            .where(and_(*clauses))
            .order_by(MarketData.ts.asc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt).all())


class MarketTradeRepository(Repository[MarketTrade]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, MarketTrade)

    def find_trade(
        self,
        *,
        source: str,
        symbol: str,
        trade_id: str | None,
        event_time: datetime,
    ) -> Optional[MarketTrade]:
        clauses = [
            MarketTrade.source == source,
            MarketTrade.symbol == symbol,
            MarketTrade.event_time == event_time,
        ]
        if trade_id is not None:
            clauses.append(MarketTrade.trade_id == trade_id)
        stmt = select(MarketTrade).where(and_(*clauses))
        return self.session.scalars(stmt).first()

    def upsert_trade(self, entity: MarketTrade) -> MarketTrade:
        existing = self.find_trade(
            source=entity.source,
            symbol=entity.symbol,
            trade_id=entity.trade_id,
            event_time=entity.event_time,
        )
        if existing is not None:
            return existing
        return self.add(entity)


class MarketQuoteRepository(Repository[MarketQuote]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, MarketQuote)

    def find_quote(
        self,
        *,
        source: str,
        symbol: str,
        event_time: datetime,
    ) -> Optional[MarketQuote]:
        stmt = select(MarketQuote).where(
            and_(
                MarketQuote.source == source,
                MarketQuote.symbol == symbol,
                MarketQuote.event_time == event_time,
            )
        )
        return self.session.scalars(stmt).first()

    def upsert_quote(self, entity: MarketQuote) -> MarketQuote:
        existing = self.find_quote(
            source=entity.source,
            symbol=entity.symbol,
            event_time=entity.event_time,
        )
        if existing is not None:
            return existing
        return self.add(entity)

    def latest(self, symbol: str, source: str | None = None) -> Optional[MarketQuote]:
        clauses = [MarketQuote.symbol == symbol]
        if source:
            clauses.append(MarketQuote.source == source)
        stmt = (
            select(MarketQuote)
            .where(and_(*clauses))
            .order_by(MarketQuote.event_time.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()


class DataQualityEventRepository(Repository[DataQualityEvent]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, DataQualityEvent)

    def count_by_rule(self, rule: str | None = None) -> int:
        stmt = select(DataQualityEvent)
        if rule:
            stmt = stmt.where(DataQualityEvent.rule == rule)
        return len(list(self.session.scalars(stmt).all()))


class SignalRepository(Repository[Signal]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Signal)


class TradeRepository(Repository[Trade]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Trade)


class PositionRepository(Repository[Position]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Position)


class PortfolioRepository(Repository[Portfolio]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Portfolio)


class SystemEventRepository(Repository[SystemEvent]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, SystemEvent)


class StrategyVersionRepository(Repository[StrategyVersion]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, StrategyVersion)
