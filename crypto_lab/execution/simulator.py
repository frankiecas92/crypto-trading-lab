"""Historical execution simulator (backtest only — not a live/paper broker).

MARKET: fill at reference mid (next-bar open) ± spread/slippage.
LIMIT: fill only if the bar range trades through the limit; otherwise unfilled.
If there is no bar / no OHLC range for a limit, mark a limitation — never invent.

This module does not place real orders. Live paths remain blocked by safety.py.
"""

from __future__ import annotations

from typing import Optional

from crypto_lab.backtest.costs import CostModel, apply_partial, compute_fill_quote
from crypto_lab.backtest.types import Bar, Fill, PendingOrder
from crypto_lab.execution.safety import guard_live_trading


class ExecutionSimulator:
    """Fill helper used by the backtest engine."""

    def __init__(self, cost: CostModel) -> None:
        guard_live_trading()
        self.cost = cost

    def try_fill(
        self,
        order: PendingOrder,
        bar: Bar | None,
        *,
        qty: float,
        use_open: bool = True,
    ) -> tuple[Optional[Fill], Optional[str]]:
        """Attempt a fill on ``bar``.

        Returns (Fill, None) on success, (None, reason) if not filled.
        Never invents a fill when ``bar`` is missing.
        """
        if bar is None:
            return None, "NO_BAR: insufficient fill info — not inventing a fill"
        if qty <= 0:
            return None, "ZERO_QTY"

        if order.order_type == "LIMIT":
            fill, why = self._try_limit(order, bar, qty, use_open=use_open)
            return fill, why

        return self._market_fill(order, bar, qty, use_open=use_open), None

    def _market_fill(
        self, order: PendingOrder, bar: Bar, qty: float, *, use_open: bool
    ) -> Fill:
        intended = qty
        qty, partial = apply_partial(qty, self.cost)
        q = compute_fill_quote(order.side, bar, self.cost, qty=qty, use_open=use_open)
        return Fill(
            index=-1,  # engine sets
            event_time=bar.event_time,
            symbol=bar.symbol,
            side=order.side,
            qty=qty,
            mid_price=q.mid,
            fill_price=q.fill_price,
            fee=q.fee,
            spread_cost=q.spread_cost,
            slippage_cost=q.slippage_cost,
            order_type="MARKET",
            reason=order.reason,
            partial=partial,
            intended_qty=intended,
            limitation=q.limitation,
        )

    def _try_limit(
        self,
        order: PendingOrder,
        bar: Bar,
        qty: float,
        *,
        use_open: bool,
    ) -> tuple[Optional[Fill], Optional[str]]:
        limit = order.limit_price
        if limit is None or limit <= 0:
            return None, "LIMIT_NO_PRICE: insufficient fill info — not inventing a fill"
        # Need an OHLC range to know if the limit traded.
        if bar.high <= 0 or bar.low <= 0:
            return None, "LIMIT_NO_RANGE: insufficient OHLC — not inventing a fill"

        if order.side == "BUY":
            touched = bar.low <= limit
        else:
            touched = bar.high >= limit
        if not touched:
            return None, "LIMIT_NOT_TOUCHED"

        intended = qty
        qty, partial = apply_partial(qty, self.cost)
        q = compute_fill_quote(
            order.side, bar, self.cost, qty=qty, use_open=use_open, liquidity="maker"
        )
        # Honor limit: buy cannot pay more than limit; sell cannot receive less.
        if order.side == "BUY":
            # Gap through: open below limit → fill at open (better); else limit.
            fill_price = min(q.fill_price, limit)
            if bar.open < limit:
                fill_price = min(bar.open, limit)
        else:
            fill_price = max(q.fill_price, limit)
            if bar.open > limit:
                fill_price = max(bar.open, limit)

        # Recompute fee from the actual fill price; LIMIT fills use maker fee.
        # (MARKET path uses taker via compute_fill_quote default.)
        fee = fill_price * qty * self.cost.fee_rate(liquidity="maker")
        return (
            Fill(
                index=-1,
                event_time=bar.event_time,
                symbol=bar.symbol,
                side=order.side,
                qty=qty,
                mid_price=q.mid,
                fill_price=fill_price,
                fee=fee,
                spread_cost=q.spread_cost,
                slippage_cost=q.slippage_cost,
                order_type="LIMIT",
                reason=order.reason,
                partial=partial,
                intended_qty=intended,
                limitation=(
                    "LIMIT fill on OHLCV: assumed filled if low<=limit (buy) or "
                    "high>=limit (sell); price = min/max(model, limit) / gap open"
                ),
            ),
            None,
        )
