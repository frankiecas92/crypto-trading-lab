"""Configurable cost model: fees, spread, slippage, latency; optional partial fills.

Cost profiles (configurable research assumptions — NOT official Binance fee lookups):
  COST_PROFILE_CONSERVATIVE / BASE / STRESS
  BASE may equal current demo: fee 10bps, spread 4bps, slip 2bps
  maker_fee_bps vs taker_fee_bps exist (may default equal; do not assume forever).

Exchange fees vs market friction (keep separate):
  fee_bps / maker_fee_bps / taker_fee_bps = EXCHANGE FEES
  spread_bps + slippage_bps = MARKET FRICTION
  MARKET fills use taker; LIMIT fills use maker.
  Fee is applied per fill (entry AND exit). Spread is half on buy + half on sell.

Gross vs net:
  mid/open is the frictionless reference (gross).
  fill_price includes spread and slippage; fee is cash, not in the price.
  PRIMARY reported metric is net after costs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, Dict, Literal

from crypto_lab.backtest.timeframes import latency_ms_to_bars
from crypto_lab.backtest.types import Bar


Side = Literal["BUY", "SELL"]
LiquidityRole = Literal["maker", "taker"]


class CostProfileName(str, Enum):
    CONSERVATIVE = "COST_PROFILE_CONSERVATIVE"
    BASE = "COST_PROFILE_BASE"
    STRESS = "COST_PROFILE_STRESS"


@dataclass(frozen=True)
class CostModel:
    fee_bps: float = 10.0  # shorthand / legacy; used when maker/taker unset
    spread_bps: float = 2.0  # full bid-ask in bps of mid (half charged each side)
    slippage_bps: float = 1.0
    latency_bars: int = 0  # extra bars after the standard next-bar fill
    latency_ms: float = 0.0  # converted to bars via timeframe if latency_bars==0
    partial_fill_ratio: float = 1.0  # 1 = full; (0,1) = partial; 0 = no fill
    # Prepared fields — may default equal to fee_bps; do not assume forever equal.
    maker_fee_bps: float | None = None
    taker_fee_bps: float | None = None
    profile_name: str | None = None

    def __post_init__(self) -> None:
        if self.fee_bps < 0 or self.spread_bps < 0 or self.slippage_bps < 0:
            raise ValueError("cost bps must be >= 0")
        if self.latency_bars < 0 or self.latency_ms < 0:
            raise ValueError("latency must be >= 0")
        if not (0.0 <= self.partial_fill_ratio <= 1.0):
            raise ValueError("partial_fill_ratio must be in [0, 1]")
        for name, val in (("maker_fee_bps", self.maker_fee_bps), ("taker_fee_bps", self.taker_fee_bps)):
            if val is not None and val < 0:
                raise ValueError(f"{name} must be >= 0")

    def effective_maker_fee_bps(self) -> float:
        return self.fee_bps if self.maker_fee_bps is None else float(self.maker_fee_bps)

    def effective_taker_fee_bps(self) -> float:
        return self.fee_bps if self.taker_fee_bps is None else float(self.taker_fee_bps)

    def extra_latency_bars(self, timeframe: str) -> int:
        if self.latency_bars > 0:
            return self.latency_bars
        return latency_ms_to_bars(self.latency_ms, timeframe)

    def fee_rate(self, *, liquidity: LiquidityRole = "taker") -> float:
        """Default MARKET fills use taker; LIMIT may pass liquidity='maker'."""
        bps = self.effective_taker_fee_bps() if liquidity == "taker" else self.effective_maker_fee_bps()
        return bps / 10_000.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["effective_maker_fee_bps"] = self.effective_maker_fee_bps()
        d["effective_taker_fee_bps"] = self.effective_taker_fee_bps()
        d["profile_name"] = self.profile_name
        return d


# BASE matches current demo defaults: fee 10bps, spread 4bps, slip 2bps
_PROFILE_SPECS: Dict[CostProfileName, Dict[str, float]] = {
    CostProfileName.BASE: {
        "fee_bps": 10.0,
        "spread_bps": 4.0,
        "slippage_bps": 2.0,
        "maker_fee_bps": 10.0,
        "taker_fee_bps": 10.0,
    },
    CostProfileName.CONSERVATIVE: {
        "fee_bps": 15.0,
        "spread_bps": 6.0,
        "slippage_bps": 4.0,
        "maker_fee_bps": 12.0,
        "taker_fee_bps": 15.0,
    },
    CostProfileName.STRESS: {
        "fee_bps": 25.0,
        "spread_bps": 12.0,
        "slippage_bps": 10.0,
        "maker_fee_bps": 20.0,
        "taker_fee_bps": 25.0,
    },
}


def parse_cost_profile_name(name: str | CostProfileName | None) -> CostProfileName:
    if name is None:
        return CostProfileName.BASE
    if isinstance(name, CostProfileName):
        return name
    raw = str(name).strip().upper()
    aliases = {
        "BASE": CostProfileName.BASE,
        "COST_PROFILE_BASE": CostProfileName.BASE,
        "CONSERVATIVE": CostProfileName.CONSERVATIVE,
        "COST_PROFILE_CONSERVATIVE": CostProfileName.CONSERVATIVE,
        "STRESS": CostProfileName.STRESS,
        "COST_PROFILE_STRESS": CostProfileName.STRESS,
    }
    if raw not in aliases:
        raise ValueError(f"unknown cost profile {name!r}; expected BASE/CONSERVATIVE/STRESS")
    return aliases[raw]


def get_cost_profile(
    name: str | CostProfileName = CostProfileName.BASE,
    *,
    latency_bars: int = 0,
    latency_ms: float = 0.0,
    partial_fill_ratio: float = 1.0,
) -> CostModel:
    profile = parse_cost_profile_name(name)
    spec = _PROFILE_SPECS[profile]
    return CostModel(
        fee_bps=spec["fee_bps"],
        spread_bps=spec["spread_bps"],
        slippage_bps=spec["slippage_bps"],
        maker_fee_bps=spec["maker_fee_bps"],
        taker_fee_bps=spec["taker_fee_bps"],
        latency_bars=latency_bars,
        latency_ms=latency_ms,
        partial_fill_ratio=partial_fill_ratio,
        profile_name=profile.value,
    )


def scale_cost_model(base: CostModel, factor: float) -> CostModel:
    """Scale fee/spread/slippage by factor; preserve latency/partial/profile tag."""
    if factor < 0:
        raise ValueError("factor must be >= 0")
    return replace(
        base,
        fee_bps=base.fee_bps * factor,
        spread_bps=base.spread_bps * factor,
        slippage_bps=base.slippage_bps * factor,
        maker_fee_bps=(
            None if base.maker_fee_bps is None else base.maker_fee_bps * factor
        ),
        taker_fee_bps=(
            None if base.taker_fee_bps is None else base.taker_fee_bps * factor
        ),
        profile_name=base.profile_name,
    )


@dataclass(frozen=True)
class FillQuote:
    mid: float
    fill_price: float
    fee: float
    spread_cost: float
    slippage_cost: float
    used_quotes: bool
    limitation: str | None


def reference_mid(bar: Bar, *, use_open: bool = True) -> float:
    """Reference mid for an OHLCV fill attempt.

    Prefer explicit bid/ask mid when both present; else next-bar OPEN
    (OHLCV-only assumption). use_open=False uses close (tests / marks).
    """
    if bar.bid is not None and bar.ask is not None and bar.bid > 0 and bar.ask > 0:
        return (bar.bid + bar.ask) / 2.0
    return bar.open if use_open else bar.close


def compute_fill_quote(
    side: str,
    bar: Bar,
    cost: CostModel,
    *,
    qty: float,
    use_open: bool = True,
    liquidity: LiquidityRole = "taker",
) -> FillQuote:
    """Compute fill price and cash costs. Does not invent missing bars.

    BUY:  fill = mid + half_spread + slippage   (or ask + slippage if quotes)
    SELL: fill = mid - half_spread - slippage   (or bid - slippage if quotes)
    fee  = fill_price * qty * fee_rate (taker by default for MARKET)
    spread_cost / slippage_cost are cash amounts (qty * price_delta).
    """
    side_u = side.upper()
    if side_u not in {"BUY", "SELL"}:
        raise ValueError(f"fill side must be BUY/SELL, got {side!r}")
    if qty < 0:
        raise ValueError("qty must be >= 0")

    used_quotes = bar.bid is not None and bar.ask is not None and bar.bid > 0 and bar.ask > 0
    limitation = None
    if used_quotes:
        mid = (bar.bid + bar.ask) / 2.0  # type: ignore[operator]
        half_spread = (bar.ask - bar.bid) / 2.0  # type: ignore[operator]
    else:
        mid = bar.open if use_open else bar.close
        half_spread = mid * (cost.spread_bps / 10_000.0) / 2.0
        limitation = (
            "OHLCV-only fill: next-bar open + configured spread_bps model "
            "(no venue bid/ask on this bar)"
        )

    slip = mid * (cost.slippage_bps / 10_000.0)
    if side_u == "BUY":
        if used_quotes:
            fill_price = float(bar.ask) + slip  # type: ignore[arg-type]
            half_spread = float(bar.ask) - mid  # type: ignore[arg-type]
        else:
            fill_price = mid + half_spread + slip
    else:
        if used_quotes:
            fill_price = float(bar.bid) - slip  # type: ignore[arg-type]
            half_spread = mid - float(bar.bid)  # type: ignore[arg-type]
        else:
            fill_price = mid - half_spread - slip

    if fill_price <= 0:
        raise ValueError("computed fill_price must be > 0")

    notional = fill_price * qty
    fee = notional * cost.fee_rate(liquidity=liquidity)
    spread_cost = abs(half_spread) * qty
    slippage_cost = abs(slip) * qty
    return FillQuote(
        mid=mid,
        fill_price=fill_price,
        fee=fee,
        spread_cost=spread_cost,
        slippage_cost=slippage_cost,
        used_quotes=used_quotes,
        limitation=limitation,
    )


def apply_partial(qty: float, cost: CostModel) -> tuple[float, bool]:
    if cost.partial_fill_ratio >= 1.0:
        return qty, False
    if cost.partial_fill_ratio <= 0.0:
        return 0.0, True
    return qty * cost.partial_fill_ratio, True
