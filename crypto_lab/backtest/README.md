# Backtest — Phase 3 Strategy Research Lab

Historical simulation only. **No live loop, no paper broker, no real orders.**

Pipeline: `DATA → VALIDATE → FEATURES → STRATEGY → SIGNAL → SIZING → EXEC SIM → COSTS → PERFORMANCE → ROBUSTNESS → REPORT`

See [`docs/STRATEGY_RESEARCH_LAB.md`](../../docs/STRATEGY_RESEARCH_LAB.md).

## Anti look-ahead

`DataView` exposes only bars with `event_time <= T` (closed bar at decision index).
Future OHLCV is **UNKNOWN AT T**. Tests poison future closes/highs/lows/volumes.
