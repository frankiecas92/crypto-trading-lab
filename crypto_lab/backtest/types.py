"""Shared research types: Bar, signals, fills, trades, portfolio snapshot.

OHLCV-only fill assumption (explicit limitation):
  Decision at close of bar T; MARKET fill attempted at next-bar OPEN
  plus spread/slippage model. Quote bid/ask used when present on the fill bar.
  Never invent fills from empty books / missing bars.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence


SUPPORTED_SYMBOLS = ("BTCUSDT", "ETHUSDT")
SIDES = ("BUY", "SELL", "HOLD", "FLAT")
ORDER_TYPES = ("MARKET", "LIMIT")
REGIME_LABELS = ("BULL", "BEAR", "SIDEWAYS", "HIGH_VOL", "LOW_VOL", "UNKNOWN")


@dataclass(frozen=True)
class Bar:
    """One closed candle. event_time is the exchange event (bar open or close
    as stored by the Data Engine — we treat it as the candle identity time).
    Decision at index i uses this bar as the last CLOSED bar.
    """

    symbol: str
    event_time: datetime
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str = "synthetic"
    received_at: datetime | None = None
    bid: float | None = None
    ask: float | None = None

    @property
    def mid(self) -> float:
        if self.bid is not None and self.ask is not None and self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return self.close

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid


@dataclass(frozen=True)
class StrategySignal:
    """Strategy output at decision time T. Does not include a fill price."""

    side: str  # BUY / SELL / HOLD / FLAT
    strength: float = 1.0
    reason: str = ""
    order_type: str = "MARKET"
    limit_price: float | None = None
    symbol: str | None = None

    def __post_init__(self) -> None:
        side = self.side.upper()
        if side not in SIDES:
            raise ValueError(f"Invalid side {self.side!r}")
        ot = self.order_type.upper()
        if ot not in ORDER_TYPES:
            raise ValueError(f"Invalid order_type {self.order_type!r}")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "order_type", ot)


@dataclass
class PendingOrder:
    side: str
    due_index: int
    signal_index: int
    order_type: str = "MARKET"
    qty: float | None = None  # None = size at fill time
    limit_price: float | None = None
    reason: str = ""
    reduce_only: bool = False


@dataclass
class Fill:
    index: int
    event_time: datetime
    symbol: str
    side: str
    qty: float
    mid_price: float
    fill_price: float
    fee: float
    spread_cost: float
    slippage_cost: float
    order_type: str
    reason: str
    partial: bool = False
    intended_qty: float = 0.0
    limitation: str | None = None


@dataclass
class TradeRecord:
    """Round-trip (or still-open) trade for metrics / MC."""

    symbol: str
    side: str  # LONG / SHORT
    entry_index: int
    exit_index: int | None
    entry_time: datetime
    exit_time: datetime | None
    entry_price: float
    exit_price: float | None
    qty: float
    gross_pnl: float
    fees: float
    slippage: float
    spread_cost: float
    net_pnl: float
    bars_held: int
    mfe: float
    mae: float
    exit_reason: str
    still_open: bool = False


@dataclass
class EquityPoint:
    index: int
    event_time: datetime
    equity: float
    cash: float
    qty: float
    close: float
    drawdown: float = 0.0


@dataclass
class PortfolioState:
    cash: float
    qty: float = 0.0
    avg_entry: float = 0.0
    peak_price: float = 0.0  # for trailing
    trough_price: float = 0.0
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    slippage_paid: float = 0.0
    spread_paid: float = 0.0
    entry_index: int | None = None
    entry_time: datetime | None = None

    def equity(self, price: float) -> float:
        return self.cash + self.qty * price

    @property
    def is_long(self) -> bool:
        return self.qty > 0

    @property
    def is_flat(self) -> bool:
        return self.qty == 0

    @property
    def is_short(self) -> bool:
        return self.qty < 0


@dataclass
class BacktestResult:
    strategy_id: str
    strategy_version: str
    symbol: str
    timeframe: str
    parameters: Dict[str, Any]
    initial_capital: float
    equity_curve: List[EquityPoint] = field(default_factory=list)
    fills: List[Fill] = field(default_factory=list)
    trades: List[TradeRecord] = field(default_factory=list)
    signals: List[tuple[int, StrategySignal]] = field(default_factory=list)
    unfilled: List[Dict[str, Any]] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    cost_assumptions: Dict[str, Any] = field(default_factory=dict)
    active_stops: Dict[str, Any] = field(default_factory=dict)
    split_name: str = "full"
    seed: int | None = None
    dataset_id: str = ""
    regime_counts: Dict[str, int] = field(default_factory=dict)

    @property
    def final_equity(self) -> float:
        if not self.equity_curve:
            return self.initial_capital
        return self.equity_curve[-1].equity

    @property
    def net_return(self) -> float:
        if self.initial_capital <= 0:
            return 0.0
        return (self.final_equity / self.initial_capital) - 1.0


def bars_from_ohlcv(
    *,
    symbol: str,
    timeframe: str,
    times: Sequence[datetime],
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float] | None = None,
    source: str = "synthetic",
    bids: Sequence[float | None] | None = None,
    asks: Sequence[float | None] | None = None,
) -> List[Bar]:
    n = len(times)
    vols = list(volumes) if volumes is not None else [0.0] * n
    out: List[Bar] = []
    for i in range(n):
        out.append(
            Bar(
                symbol=symbol,
                event_time=times[i],
                timeframe=timeframe,
                open=float(opens[i]),
                high=float(highs[i]),
                low=float(lows[i]),
                close=float(closes[i]),
                volume=float(vols[i]),
                source=source,
                bid=None if bids is None else bids[i],
                ask=None if asks is None else asks[i],
            )
        )
    return out
