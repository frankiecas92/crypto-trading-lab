# Crypto Trading Lab — Phase 4B-REAL (Historical Strategy Evaluation)

**ES / EN** — Laboratorio de investigación: motor de datos públicos + backtest histórico.  
**Phase 4B-REAL**: research campaign on **real** Binance Spot history (BTC/ETH) + Phase 4A/4B protocol + Phase 3 research + Phase 2 Data Engine. Phase 5 **not** justified by current evidence.  
**No** trading real, **no** órdenes reales, **no** bucle paper en vivo, **no** apalancamiento, **no** API keys.

- Data Engine: [`docs/DATA_ENGINE.md`](docs/DATA_ENGINE.md)
- Research lab: [`docs/STRATEGY_RESEARCH_LAB.md`](docs/STRATEGY_RESEARCH_LAB.md)
- Historical datasets: [`docs/HISTORICAL_DATA.md`](docs/HISTORICAL_DATA.md)
- Research campaign: [`docs/RESEARCH_CAMPAIGN.md`](docs/RESEARCH_CAMPAIGN.md)
- Real campaign report: [`docs/RESEARCH_CAMPAIGN_REAL.md`](docs/RESEARCH_CAMPAIGN_REAL.md)
- Data & cost audit (4C): [`docs/DATA_COST_AUDIT.md`](docs/DATA_COST_AUDIT.md)

---

## Alcance / Scope

| Incluido ✅ | No incluido ❌ |
|-------------|----------------|
| Phase 1 infra + Phase 2 Data Engine | Live trading / real orders |
| Historical backtest + costs + metrics | Paper broker live loop |
| 4 benchmark controls (BH BTC/ETH, SMA, momentum) | Edge-hunting / auto-optimize |
| IS/OOS, walk-forward, robustness, Monte Carlo | Leverage / money / agents |
| Experiment registry (SQLite + JSON) | Trading API keys |
| Historical download / validate / snapshot (4A) | EDGE_CONFIRMED / PROFITABLE claims |
| Research campaign protocol (4B / 4B-REAL) | Decision Engine / Paper Trader / autonomy |

---

## Instalación

```bash
cd crypto-trading-lab
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
cp .env.example .env
```

Defaults: `MODE=PAPER`, `LIVE_TRADING=FALSE`, symbols `BTCUSDT,ETHUSDT`. **No API keys.**

## CLI

```bash
crypto-lab version
crypto-lab init-db
crypto-lab health --json
crypto-lab data fetch-rest --limit 5
crypto-lab data validate-sample
crypto-lab experiment demo --bars 180 --seed 7
crypto-lab historical-data download --timeframe 1h --max-bars 72
crypto-lab historical-data status
crypto-lab research-campaign run --fixture --seed 7 --no-persist
crypto-lab research-campaign run-real --years 2 --max-bars 20000
```

## Tests

```bash
pytest -v
RUN_LIVE_DATA_TESTS=1 pytest -v -m live   # optional public REST
```

## Seguridad

- `guard_live_trading()` fails closed on any live path
- Health requires `MODE=PAPER` and `LIVE_TRADING=False`
- Public market-data hosts only; no account / order endpoints

## License

MIT (proyecto de investigación personal).
