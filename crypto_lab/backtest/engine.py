"""Backtest engine: STRATEGY → SIGNAL → SIZING → EXEC SIM → COSTS → EQUITY.

Anti look-ahead:
  - Strategy sees DataView(prefix at i) only.
  - MARKET/LIMIT fills occur on a later bar (i + 1 + latency), never on future
    unknown bars beyond that scheduled index.
  - Features are computed on the view.

OHLCV-only limitation (always recorded):
  Decision at close of bar i; fill at open of bar i+1+latency plus spread model
  unless bid/ask are present on the fill bar.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from crypto_lab.backtest.costs import CostModel
from crypto_lab.backtest.data_view import DataView
from crypto_lab.backtest.metrics import compute_metrics
from crypto_lab.backtest.regimes import label_regimes
from crypto_lab.backtest.types import (
    BacktestResult,
    Bar,
    EquityPoint,
    Fill,
    PendingOrder,
    PortfolioState,
    StrategySignal,
    TradeRecord,
)
from crypto_lab.execution.safety import guard_live_trading
from crypto_lab.execution.simulator import ExecutionSimulator
from crypto_lab.risk.sizing import SizingPolicy, size_quantity
from crypto_lab.risk.stops import StopHit, StopPolicy, check_stops
from crypto_lab.strategies.base import Strategy


OHLCV_LIMITATION = (
    "OHLCV-only execution: signal at close of bar T; MARKET fill at open of "
    "bar T+1+latency plus configured spread/slippage. Prefer bid/ask when "
    "present. Never invent fills without a bar."
)


class BacktestEngine:
    def __init__(
        self,
        *,
        cost: CostModel | None = None,
        sizing: SizingPolicy | None = None,
        stops: StopPolicy | None = None,
        initial_capital: float = 100_000.0,
        allow_short: bool = False,
    ) -> None:
        guard_live_trading()
        if initial_capital <= 0:
            raise ValueError("initial_capital must be > 0")
        self.cost = cost or CostModel()
        self.sizing = sizing or SizingPolicy()
        self.stops = stops or StopPolicy()
        self.initial_capital = float(initial_capital)
        self.allow_short = bool(allow_short)
        if self.allow_short and self.sizing.max_exposure > 1.0:
            raise ValueError("no leverage: max_exposure must be <= 1")

    def run(
        self,
        strategy: Strategy,
        bars: Sequence[Bar],
        *,
        split_name: str = "full",
        seed: int | None = None,
        dataset_id: str = "",
        close_at_end: bool = True,
        trade_from_index: int = 0,
    ) -> BacktestResult:
        guard_live_trading()
        if not bars:
            raise ValueError("bars must be non-empty")
        symbol = bars[0].symbol
        timeframe = bars[0].timeframe
        req = strategy.required_symbol()
        limitations = [OHLCV_LIMITATION]
        if req and req != symbol:
            limitations.append(
                f"strategy required_symbol={req} != series symbol={symbol}; no trades"
            )

        sim = ExecutionSimulator(self.cost)
        extra_lat = self.cost.extra_latency_bars(timeframe)
        # Standard: fill on next bar; extra_lat adds further delay.
        fill_delay = 1 + extra_lat

        port = PortfolioState(cash=self.initial_capital)
        pending: List[PendingOrder] = []
        fills: List[Fill] = []
        trades: List[TradeRecord] = []
        signals: List[tuple[int, StrategySignal]] = []
        unfilled: List[Dict[str, Any]] = []
        curve: List[EquityPoint] = []
        peak_eq = self.initial_capital
        open_mfe = 0.0
        open_mae = 0.0

        n = len(bars)
        for i, bar in enumerate(bars):
            # --- 1) due fills at this bar (from prior decisions) ---
            due = [o for o in pending if o.due_index == i]
            pending = [o for o in pending if o.due_index != i]
            for order in due:
                self._execute_order(
                    order,
                    bar,
                    i,
                    port,
                    sim,
                    fills,
                    trades,
                    unfilled,
                    open_mfe_mae=(open_mfe, open_mae),
                )
            # leftover pending with due_index < i should not happen
            expired = [o for o in pending if o.due_index < i]
            for o in expired:
                unfilled.append({"reason": "EXPIRED", "order": o.reason, "index": i})
            pending = [o for o in pending if o.due_index >= i]

            # --- 2) stops on this bar (after open fills) ---
            if not port.is_flat:
                if port.qty > 0:
                    port.peak_price = max(port.peak_price, bar.high)
                    port.trough_price = (
                        min(port.trough_price, bar.low) if port.trough_price > 0 else bar.low
                    )
                else:
                    port.trough_price = (
                        min(port.trough_price, bar.low) if port.trough_price > 0 else bar.low
                    )
                    port.peak_price = max(port.peak_price, bar.high)
                # MFE/MAE vs entry (price units * qty sign)
                if port.avg_entry > 0 and port.qty != 0:
                    fav = (bar.high - port.avg_entry) if port.qty > 0 else (port.avg_entry - bar.low)
                    adv = (port.avg_entry - bar.low) if port.qty > 0 else (bar.high - port.avg_entry)
                    open_mfe = max(open_mfe, fav * abs(port.qty))
                    open_mae = max(open_mae, adv * abs(port.qty))
                hit = check_stops(port, bar, self.stops)
                if hit is not None:
                    self._close_position(
                        port,
                        bar,
                        i,
                        fills,
                        trades,
                        reason=hit.reason,
                        fill_price_override=hit.fill_price,
                        limitation=hit.limitation,
                        mfe=open_mfe,
                        mae=open_mae,
                    )
                    open_mfe = 0.0
                    open_mae = 0.0
                    if hit.limitation:
                        limitations.append(hit.limitation)

            # --- 3) mark equity at close ---
            eq = port.equity(bar.close)
            peak_eq = max(peak_eq, eq)
            dd = (eq / peak_eq - 1.0) if peak_eq > 0 else 0.0
            curve.append(
                EquityPoint(
                    index=i,
                    event_time=bar.event_time,
                    equity=eq,
                    cash=port.cash,
                    qty=port.qty,
                    close=bar.close,
                    drawdown=dd,
                )
            )

            # --- 4) signal on CLOSED bar i (no future) ---
            view = DataView(bars, i)
            sig = strategy.generate_signals(view)
            signals.append((i, sig))
            if req and req != symbol:
                continue
            if i < trade_from_index:
                continue
            if sig.side in {"HOLD"}:
                continue

            due_idx = i + fill_delay
            if sig.side == "BUY":
                if port.is_long:
                    continue
                if port.is_short:
                    pending.append(
                        PendingOrder(
                            side="BUY",
                            due_index=due_idx,
                            signal_index=i,
                            order_type=sig.order_type,
                            limit_price=sig.limit_price,
                            reason=sig.reason or "cover_short",
                            reduce_only=True,
                        )
                    )
                pending.append(
                    PendingOrder(
                        side="BUY",
                        due_index=due_idx,
                        signal_index=i,
                        order_type=sig.order_type,
                        limit_price=sig.limit_price,
                        reason=sig.reason or "entry_long",
                        reduce_only=False,
                    )
                )
            elif sig.side in {"SELL", "FLAT"}:
                if port.is_flat and not self.allow_short:
                    continue
                if port.is_long:
                    pending.append(
                        PendingOrder(
                            side="SELL",
                            due_index=due_idx,
                            signal_index=i,
                            order_type=sig.order_type,
                            limit_price=sig.limit_price,
                            reason=sig.reason or "exit_long",
                            reduce_only=True,
                        )
                    )
                elif self.allow_short and port.is_flat:
                    pending.append(
                        PendingOrder(
                            side="SELL",
                            due_index=due_idx,
                            signal_index=i,
                            order_type=sig.order_type,
                            limit_price=sig.limit_price,
                            reason=sig.reason or "entry_short",
                            reduce_only=False,
                        )
                    )

        # pending beyond last bar
        for o in pending:
            if o.due_index >= n:
                unfilled.append(
                    {
                        "reason": "NO_BAR",
                        "detail": "insufficient fill info — not inventing a fill",
                        "signal_index": o.signal_index,
                        "due_index": o.due_index,
                    }
                )

        if close_at_end and not port.is_flat:
            last = bars[-1]
            self._close_position(
                port,
                last,
                n - 1,
                fills,
                trades,
                reason="EOD_MARK_CLOSE",
                fill_price_override=None,
                limitation="Closed at last bar using cost model (research close_at_end)",
                mfe=open_mfe,
                mae=open_mae,
                use_open=False,
            )
            # refresh last equity after forced close
            eq = port.equity(last.close)
            peak_eq = max(peak_eq, eq)
            dd = (eq / peak_eq - 1.0) if peak_eq > 0 else 0.0
            curve[-1] = EquityPoint(
                index=n - 1,
                event_time=last.event_time,
                equity=eq,
                cash=port.cash,
                qty=port.qty,
                close=last.close,
                drawdown=dd,
            )

        regimes = label_regimes(bars)
        counts: Dict[str, int] = {}
        for r in regimes:
            counts[r] = counts.get(r, 0) + 1

        result = BacktestResult(
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version(),
            symbol=symbol,
            timeframe=timeframe,
            parameters=strategy.parameters(),
            initial_capital=self.initial_capital,
            equity_curve=curve,
            fills=fills,
            trades=trades,
            signals=signals,
            unfilled=unfilled,
            limitations=list(dict.fromkeys(limitations)),
            cost_assumptions=self.cost.to_dict(),
            active_stops=self.stops.active(),
            split_name=split_name,
            seed=seed,
            dataset_id=dataset_id,
            regime_counts=counts,
        )
        result.metrics = compute_metrics(result)
        return result

    def _execute_order(
        self,
        order: PendingOrder,
        bar: Bar,
        index: int,
        port: PortfolioState,
        sim: ExecutionSimulator,
        fills: List[Fill],
        trades: List[TradeRecord],
        unfilled: List[Dict[str, Any]],
        open_mfe_mae: tuple[float, float],
    ) -> None:
        fee_rate = self.cost.fee_rate()
        if order.reduce_only:
            qty = abs(port.qty)
            if qty <= 0:
                unfilled.append({"reason": "FLAT_ALREADY", "index": index})
                return
        else:
            stop_dist = self.stops.stop_distance_for_price(bar.open)
            try:
                qty = size_quantity(
                    portfolio=port,
                    price=bar.open,
                    policy=self.sizing,
                    fee_rate=fee_rate,
                    stop_distance=stop_dist,
                )
            except ValueError:
                # FIXED_RISK without stop: fall back to notional on cash
                qty = size_quantity(
                    portfolio=port,
                    price=bar.open,
                    policy=SizingPolicy(
                        mode="FIXED_NOTIONAL",
                        notional=self.sizing.notional,
                        max_exposure=self.sizing.max_exposure,
                        max_position_qty=self.sizing.max_position_qty,
                    ),
                    fee_rate=fee_rate,
                    stop_distance=None,
                )
        fill, why = sim.try_fill(order, bar, qty=qty, use_open=True)
        if fill is None:
            unfilled.append({"reason": why or "UNFILLED", "index": index, "order": order.reason})
            return
        fill.index = index
        self._apply_fill(port, fill, bar, index, trades, open_mfe_mae)
        fills.append(fill)
        if fill.limitation:
            pass  # recorded on the fill; engine limitations already has OHLCV note

    def _apply_fill(
        self,
        port: PortfolioState,
        fill: Fill,
        bar: Bar,
        index: int,
        trades: List[TradeRecord],
        open_mfe_mae: tuple[float, float],
    ) -> None:
        if fill.qty <= 0:
            return
        if fill.side == "BUY":
            if port.qty < 0:
                # cover short
                cover = min(fill.qty, abs(port.qty))
                self._realize(port, fill, cover, index, bar, trades, open_mfe_mae, closing_side="SHORT")
                remain = fill.qty - cover
                if remain > 0:
                    self._open_long(port, fill, remain, index, bar)
            else:
                self._open_long(port, fill, fill.qty, index, bar)
        else:  # SELL
            if port.qty > 0:
                close_qty = min(fill.qty, port.qty)
                self._realize(port, fill, close_qty, index, bar, trades, open_mfe_mae, closing_side="LONG")
                remain = fill.qty - close_qty
                if remain > 0 and self.allow_short:
                    self._open_short(port, fill, remain, index, bar)
            elif self.allow_short:
                self._open_short(port, fill, fill.qty, index, bar)

    def _open_long(
        self, port: PortfolioState, fill: Fill, qty: float, index: int, bar: Bar
    ) -> None:
        cost = fill.fill_price * qty + (fill.fee * (qty / fill.qty if fill.qty else 1.0))
        # fee already on full fill; scale if partial apply of same fill
        fee_part = fill.fee if qty == fill.qty else fill.fee * (qty / fill.qty)
        slip_part = fill.slippage_cost if qty == fill.qty else fill.slippage_cost * (qty / fill.qty)
        spr_part = fill.spread_cost if qty == fill.qty else fill.spread_cost * (qty / fill.qty)
        cost = fill.fill_price * qty + fee_part
        if cost > port.cash + 1e-9:
            # belt-and-suspenders no-leverage cap
            afford = port.cash / (fill.fill_price * (1.0 + (fee_part / (fill.fill_price * qty) if qty else 0)))
            qty = max(0.0, afford)
            if qty <= 0:
                return
            fee_part = fill.fee * (qty / fill.qty) if fill.qty else 0.0
            cost = fill.fill_price * qty + fee_part
        new_qty = port.qty + qty
        if new_qty > 0:
            port.avg_entry = (
                (port.avg_entry * port.qty + fill.fill_price * qty) / new_qty
                if port.qty > 0
                else fill.fill_price
            )
        port.qty = new_qty
        port.cash -= cost
        port.fees_paid += fee_part
        port.slippage_paid += slip_part
        port.spread_paid += spr_part
        if port.entry_index is None:
            port.entry_index = index
            port.entry_time = bar.event_time
            port.peak_price = bar.high
            port.trough_price = bar.low

    def _open_short(
        self, port: PortfolioState, fill: Fill, qty: float, index: int, bar: Bar
    ) -> None:
        # Short proceeds minus fee; exposure capped by cash/equity already in sizing.
        fee_part = fill.fee if qty == fill.qty else fill.fee * (qty / fill.qty)
        slip_part = fill.slippage_cost if qty == fill.qty else fill.slippage_cost * (qty / fill.qty)
        spr_part = fill.spread_cost if qty == fill.qty else fill.spread_cost * (qty / fill.qty)
        proceeds = fill.fill_price * qty - fee_part
        port.cash += proceeds
        port.avg_entry = fill.fill_price if port.qty == 0 else port.avg_entry
        port.qty -= qty
        port.fees_paid += fee_part
        port.slippage_paid += slip_part
        port.spread_paid += spr_part
        if port.entry_index is None:
            port.entry_index = index
            port.entry_time = bar.event_time
            port.peak_price = bar.high
            port.trough_price = bar.low

    def _realize(
        self,
        port: PortfolioState,
        fill: Fill,
        qty: float,
        index: int,
        bar: Bar,
        trades: List[TradeRecord],
        open_mfe_mae: tuple[float, float],
        *,
        closing_side: str,
    ) -> None:
        fee_part = fill.fee if qty == fill.qty else fill.fee * (qty / fill.qty)
        slip_part = fill.slippage_cost if qty == fill.qty else fill.slippage_cost * (qty / fill.qty)
        spr_part = fill.spread_cost if qty == fill.qty else fill.spread_cost * (qty / fill.qty)
        if closing_side == "LONG":
            proceeds = fill.fill_price * qty - fee_part
            port.cash += proceeds
            gross = (fill.fill_price - port.avg_entry) * qty
            port.qty -= qty
        else:
            cost = fill.fill_price * qty + fee_part
            port.cash -= cost
            gross = (port.avg_entry - fill.fill_price) * qty
            port.qty += qty  # qty is negative outstanding
        net = gross - fee_part
        # spread/slip already in fill_price vs mid; report separately
        port.realized_pnl += net
        port.fees_paid += fee_part
        port.slippage_paid += slip_part
        port.spread_paid += spr_part
        entry_idx = port.entry_index if port.entry_index is not None else index
        entry_tm = port.entry_time or bar.event_time
        trades.append(
            TradeRecord(
                symbol=bar.symbol,
                side=closing_side,
                entry_index=entry_idx,
                exit_index=index,
                entry_time=entry_tm,
                exit_time=bar.event_time,
                entry_price=port.avg_entry,
                exit_price=fill.fill_price,
                qty=qty,
                gross_pnl=gross,
                fees=fee_part,
                slippage=slip_part,
                spread_cost=spr_part,
                net_pnl=net - slip_part - spr_part + slip_part + spr_part,
                # net_pnl: fill prices already include spread+slip; fees subtracted.
                # Keep net = cash impact vs entry fill: already `gross - fee`.
                bars_held=max(0, index - entry_idx),
                mfe=open_mfe_mae[0],
                mae=open_mfe_mae[1],
                exit_reason=fill.reason,
                still_open=False,
            )
        )
        # fix net_pnl explicitly
        trades[-1].net_pnl = gross - fee_part
        if abs(port.qty) < 1e-12:
            port.qty = 0.0
            port.avg_entry = 0.0
            port.entry_index = None
            port.entry_time = None
            port.peak_price = 0.0
            port.trough_price = 0.0

    def _close_position(
        self,
        port: PortfolioState,
        bar: Bar,
        index: int,
        fills: List[Fill],
        trades: List[TradeRecord],
        *,
        reason: str,
        fill_price_override: float | None,
        limitation: str | None,
        mfe: float,
        mae: float,
        use_open: bool = True,
    ) -> None:
        if port.is_flat:
            return
        side = "SELL" if port.qty > 0 else "BUY"
        qty = abs(port.qty)
        from crypto_lab.backtest.costs import compute_fill_quote

        q = compute_fill_quote(side, bar, self.cost, qty=qty, use_open=use_open)
        px = fill_price_override if fill_price_override is not None else q.fill_price
        fee = px * qty * self.cost.fee_rate()
        fill = Fill(
            index=index,
            event_time=bar.event_time,
            symbol=bar.symbol,
            side=side,
            qty=qty,
            mid_price=q.mid,
            fill_price=px,
            fee=fee,
            spread_cost=q.spread_cost,
            slippage_cost=q.slippage_cost,
            order_type="MARKET",
            reason=reason,
            partial=False,
            intended_qty=qty,
            limitation=limitation or q.limitation,
        )
        self._apply_fill(port, fill, bar, index, trades, (mfe, mae))
        fills.append(fill)
