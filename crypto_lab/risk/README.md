# Risk — Phase 3 (research sizing / stops)

- `FIXED_NOTIONAL` and `FIXED_RISK` (equity × risk/trade ÷ stop distance)
- `max_position`, `max_exposure` (≤ 1, **no leverage**)
- Optional stop-loss / take-profit / trailing stop as experimental params

Not a live risk engine. Not a kill-switch. No money management for live trading.
