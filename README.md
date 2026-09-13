# Crypto Trading Lab — Phase 1

**ES / EN** — Infraestructura base para un laboratorio de investigación y (futuro) paper-trading.  
**Phase 1 only**: configuración, logging, errores, SQLite/schema, health, CLI y guardas de seguridad.  
**No** trading real, **no** órdenes, **no** clientes Binance/exchange, **no** estrategias, **no** backtest, **no** paper broker.

---

## Alcance / Scope (Phase 1)

| Incluido ✅ | No incluido aún ❌ |
|-------------|-------------------|
| Config central (`MODE=PAPER`, `LIVE_TRADING=FALSE`) | Trading en vivo / live orders |
| Logging estructurado | API de Binance u otros exchanges |
| Jerarquía de excepciones + `SafetyError` | Estrategias / señales |
| SQLAlchemy + SQLite (7 tablas) | Paper broker / ejecución simulada |
| Repositorios CRUD delgados | Backtesting |
| Time sync local (stub offset) | Risk / money management |
| Health checks | Leverage |
| CLI: `init-db`, `health`, `version` | Fetch de mercado desde exchanges |

---

## Requisitos

- Python 3.10+
- Solo dependencias listadas en `requirements.txt` / `pyproject.toml`

## Instalación

```bash
cd crypto-trading-lab
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
# o: pip install -r requirements.txt && pip install -e .
```

Copia variables de ejemplo (sin secretos):

```bash
cp .env.example .env
```

Valores por defecto obligatorios:

- `MODE=PAPER`
- `LIVE_TRADING=FALSE`
- `DATABASE_URL=sqlite:///./data/lab.db`
- `LOG_LEVEL=INFO`
- `SYMBOLS=BTCUSDT,ETHUSDT`

**No** añadas API keys ni credenciales en Phase 1.

## Uso CLI

```bash
# Info / versión
crypto-lab version
# o
python -m crypto_lab.cli version

# Crear schema SQLite
crypto-lab init-db

# Health check
crypto-lab health
crypto-lab health --json
```

## Tests

```bash
source .venv/bin/activate
pytest -v
```

## Seguridad

- Cualquier ruta futura de live trading debe llamar a `guard_live_trading()` y **falla cerrado** con `SafetyError` / `LiveTradingBlocked`.
- Health exige `MODE=PAPER` y `LIVE_TRADING=False`.
- `.env` está en `.gitignore`. Solo existe `.env.example` con placeholders no secretos.

## Estructura

```
crypto-trading-lab/
  crypto_lab/
    config/          # pydantic-settings
    data/            # models, database, repositories
    execution/       # interfaces + safety guard (NO orders)
    monitoring/      # health checks
    strategies/      # stub Phase 2+
    risk/            # stub Phase 2+
    backtest/        # stub Phase 2+
    paper/           # stub Phase 2+ (NO paper broker)
    reports/         # stub
    cli.py
    exceptions.py
    logging_setup.py
    time_sync.py
  tests/
  .env.example
  pyproject.toml
  requirements.txt
```

## Phase 2+ (no implementar ahora)

Paper broker, estrategias, risk, backtest, datos de exchange, ejecución.

---

## License

MIT (proyecto de investigación personal).
