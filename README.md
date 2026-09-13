# Crypto Trading Lab — Phase 2 (Data Engine)

**ES / EN** — Laboratorio de investigación con motor de datos de mercado públicos.  
**Phase 2**: Data Engine (`RECEIVE → VALIDATE → NORMALIZE → STORE → MONITOR`) sobre la infraestructura Phase 1.  
**No** trading real, **no** órdenes, **no** estrategias, **no** backtest, **no** paper broker, **no** API keys.

Detalles del motor de datos: [`docs/DATA_ENGINE.md`](docs/DATA_ENGINE.md).

---

## Alcance / Scope

| Incluido ✅ | No incluido ❌ |
|-------------|----------------|
| Phase 1 infra (config, SQLite, safety, health, CLI) | Live trading / orders |
| Binance Spot public data (primary) | Strategies / signals |
| Coinbase Exchange public data (fallback) | Paper broker |
| Validator + quality events | Backtesting |
| WS + REST with reconnect / 429 backoff | Risk / money management |
| Additive schema migration | Leverage / credentials |

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
crypto-lab data health --ping --json
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
