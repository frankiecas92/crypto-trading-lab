"""Thin repository / DAO stubs — CRUD interfaces only (Phase 1)."""

from __future__ import annotations

from typing import Generic, Optional, Sequence, Type, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_lab.data.models import (
    Base,
    MarketData,
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

