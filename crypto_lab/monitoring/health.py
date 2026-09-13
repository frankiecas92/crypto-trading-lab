"""Health checks: DB, MODE=PAPER, LIVE_TRADING=False, schema present."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from sqlalchemy import text

from crypto_lab.config import Settings, get_settings
from crypto_lab.data.database import get_engine, init_db, schema_complete
from crypto_lab.exceptions import DatabaseError
from crypto_lab.time_sync import default_clock


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


@dataclass
class HealthStatus:
    healthy: bool
    mode: str
    live_trading: bool
    checks: List[CheckResult] = field(default_factory=list)
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "healthy": self.healthy,
            "mode": self.mode,
            "live_trading": self.live_trading,
            "checks": [asdict(c) for c in self.checks],
            "extras": self.extras,
        }


def run_health_checks(
    settings: Settings | None = None,
    *,
    ensure_schema: bool = True,
) -> HealthStatus:
    """Run Phase 1 health checks and return structured status."""
    cfg = settings or get_settings()
    checks: List[CheckResult] = []

    # MODE == PAPER
    mode_ok = cfg.mode.upper() == "PAPER"
    checks.append(
        CheckResult(
            name="mode_paper",
            ok=mode_ok,
            detail=f"MODE={cfg.mode}" + ("" if mode_ok else " (expected PAPER)"),
        )
    )

    # LIVE_TRADING is False
    live_ok = cfg.live_trading is False
    checks.append(
        CheckResult(
            name="live_trading_false",
            ok=live_ok,
            detail=f"LIVE_TRADING={cfg.live_trading}"
            + ("" if live_ok else " (must be False)"),
        )
    )

    # DB reachable + schema
    db_ok = False
    schema_ok = False
    db_detail = ""
    try:
        engine = get_engine(cfg.database_url)
        if ensure_schema:
            init_db(cfg.database_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
        db_detail = "database reachable"
        schema_ok = schema_complete(engine)
        schema_detail = "all expected tables present" if schema_ok else "missing tables"
    except DatabaseError as exc:
        db_detail = str(exc.message)
        schema_detail = "schema check skipped (db error)"
    except Exception as exc:  # noqa: BLE001
        db_detail = f"db error: {exc}"
        schema_detail = "schema check skipped (db error)"

    checks.append(CheckResult(name="database_reachable", ok=db_ok, detail=db_detail))
    checks.append(
        CheckResult(
            name="schema_present",
            ok=schema_ok if db_ok else False,
            detail=schema_detail if db_ok else "n/a",
        )
    )

    healthy = all(c.ok for c in checks)
    return HealthStatus(
        healthy=healthy,
        mode=cfg.mode,
        live_trading=cfg.live_trading,
        checks=checks,
        extras={
            "symbols": cfg.symbols,
            "log_level": cfg.log_level,
            "database_url": cfg.database_url,
            "clock": default_clock.status(),
        },
    )

