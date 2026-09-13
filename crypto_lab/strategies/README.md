# Strategies — Phase 3

Common ABC: `Strategy.generate_signals(data_view)`, `parameters()`, `version()`, `metadata()`.

Versions use `STRATEGY_v001` style and are stored in `strategy_versions` without overwrite.

Benchmark controls only:

1. `BuyAndHoldBTC`
2. `BuyAndHoldETH`
3. `SMACrossover`
4. `SimpleMomentum`

Strategies must not access future bars. Use `DataView` only.
