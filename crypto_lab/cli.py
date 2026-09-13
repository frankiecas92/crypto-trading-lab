"""CLI entrypoint: init-db, health, version, data fetch/validate/health."""

from __future__ import annotations

import json
import sys

import click

from crypto_lab import __phase__, __version__
from crypto_lab.config import get_settings
from crypto_lab.data.database import EXPECTED_TABLES, init_db, list_tables, reset_engine
from crypto_lab.logging_setup import setup_logging
from crypto_lab.monitoring.health import run_health_checks


@click.group()
@click.option("--log-level", default=None, help="Override LOG_LEVEL")
@click.pass_context
def main(ctx: click.Context, log_level: str | None) -> None:
    """Crypto Trading Lab — Phase 2 Data Engine CLI (public market data only)."""
    settings = get_settings()
    level = (log_level or settings.log_level).upper()
    setup_logging(level=level)
    ctx.ensure_object(dict)
    ctx.obj["settings"] = settings


@main.command("version")
def version_cmd() -> None:
    """Show package version and phase info."""
    settings = get_settings()
    click.echo(
        json.dumps(
            {
                "name": "crypto-trading-lab",
                "version": __version__,
                "phase": __phase__,
                "mode": settings.mode,
                "live_trading": settings.live_trading,
                "symbols": settings.symbols,
                "primary_provider": settings.primary_provider,
                "fallback_provider": settings.fallback_provider,
            },
            indent=2,
        )
    )


@main.command("info")
def info_cmd() -> None:
    """Alias for version / configuration summary."""
    version_cmd.callback()  # type: ignore[misc]


@main.command("init-db")
@click.option("--database-url", default=None, help="Override DATABASE_URL")
def init_db_cmd(database_url: str | None) -> None:
    """Create SQLite schema (Phase 1 + Phase 2 tables) and apply additive migrations."""
    settings = get_settings()
    url = database_url or settings.database_url
    reset_engine()
    engine = init_db(url)
    tables = sorted(list_tables(engine))
    click.echo(f"Initialized database: {url}")
    click.echo(f"Tables ({len(tables)}): {', '.join(tables)}")
    missing = sorted(EXPECTED_TABLES - set(tables))
    if missing:
        click.echo(f"WARNING missing tables: {', '.join(missing)}", err=True)
        sys.exit(1)
    click.echo("Schema OK.")


@main.command("health")
@click.option("--json", "as_json", is_flag=True, default=False, help="Print JSON status")
def health_cmd(as_json: bool) -> None:
    """Run health checks (DB, MODE=PAPER, LIVE_TRADING=False, schema)."""
    status = run_health_checks()
    if as_json:
        click.echo(json.dumps(status.to_dict(), indent=2, default=str))
    else:
        click.echo(f"healthy={status.healthy} mode={status.mode} live_trading={status.live_trading}")
        for c in status.checks:
            mark = "OK" if c.ok else "FAIL"
            click.echo(f"  [{mark}] {c.name}: {c.detail}")
    sys.exit(0 if status.healthy else 1)


@main.group("data")
def data_grp() -> None:
    """Phase 2 Data Engine commands (public market data only — no trading)."""


@data_grp.command("fetch-rest")
@click.option("--symbol", "symbols", multiple=True, help="Canonical symbol (repeatable)")
@click.option("--timeframe", default="1m", show_default=True)
@click.option("--limit", default=5, show_default=True, type=int)
@click.option("--database-url", default=None, help="Override DATABASE_URL")
def data_fetch_rest(
    symbols: tuple[str, ...],
    timeframe: str,
    limit: int,
    database_url: str | None,
) -> None:
    """Fetch sample OHLCV via public REST (BTC+ETH default), validate, and store."""
    from crypto_lab.data.database import get_session_factory, init_db, reset_engine
    from crypto_lab.data.pipeline import DataPipeline
    from crypto_lab.data.providers.router import FallbackProvider

    settings = get_settings()
    url = database_url or settings.database_url
    reset_engine()
    init_db(url)
    Session = get_session_factory(url)
    syms = list(symbols) if symbols else list(settings.symbols)
    with Session() as session:
        provider = FallbackProvider.from_settings(settings)
        try:
            pipe = DataPipeline(session, provider=provider, settings=settings)
            result = pipe.fetch_and_store_klines(syms, timeframe=timeframe, limit=limit)
            click.echo(
                json.dumps(
                    {
                        "symbols": syms,
                        "timeframe": timeframe,
                        "stored": result.stored,
                        "rejected": result.rejected,
                        "warnings": result.warnings,
                        "active_provider": provider.active.name,
                        "metrics": pipe.monitor.metrics.to_dict(),
                    },
                    indent=2,
                )
            )
        finally:
            provider.close_ws()
            if hasattr(provider.primary, "close"):
                provider.primary.close()  # type: ignore[attr-defined]
            if hasattr(provider.fallback, "close"):
                provider.fallback.close()  # type: ignore[attr-defined]


@data_grp.command("health")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.option("--ping", is_flag=True, default=False, help="Ping primary provider /time")
def data_health(as_json: bool, ping: bool) -> None:
    """Show data-feed health (provider + metrics; optional live ping)."""
    from crypto_lab.data.monitor import FeedMonitor
    from crypto_lab.data.providers.router import FallbackProvider

    settings = get_settings()
    provider = FallbackProvider.from_settings(settings)
    monitor = FeedMonitor(provider, settings=settings)
    payload = monitor.status().to_dict()
    if ping:
        try:
            t = provider.server_time()
            payload["ping"] = {"ok": True, "server_time": t.isoformat(), "active": provider.active.name}
        except Exception as exc:  # noqa: BLE001
            payload["ping"] = {"ok": False, "error": str(exc)}
            payload["healthy"] = False
    if as_json:
        click.echo(json.dumps(payload, indent=2, default=str))
    else:
        click.echo(f"data_healthy={payload['healthy']} active={payload['provider'].get('details', {}).get('active')}")
        click.echo(json.dumps(payload, indent=2, default=str))
    provider.close_ws()


@data_grp.command("validate-sample")
@click.option("--database-url", default=None)
def data_validate_sample(database_url: str | None) -> None:
    """Run validator against synthetic good/bad samples (no network)."""
    from datetime import datetime, timedelta, timezone

    from crypto_lab.data.database import get_session_factory, init_db, reset_engine
    from crypto_lab.data.pipeline import DataPipeline
    from crypto_lab.data.records import CanonicalCandle, CanonicalQuote
    from crypto_lab.data.providers.base import DataProvider, ProviderHealth

    class _Dummy(DataProvider):
        name = "dummy"

        def fetch_klines(self, symbol, *, timeframe="1m", limit=50):
            return []

        def fetch_ticker(self, symbol):
            raise NotImplementedError

        def fetch_trades(self, symbol, *, limit=50):
            return []

        def server_time(self):
            return datetime.now(timezone.utc)

        def health(self):
            return ProviderHealth(name=self.name, connected=True)

    settings = get_settings()
    url = database_url or settings.database_url
    reset_engine()
    init_db(url)
    Session = get_session_factory(url)
    now = datetime.now(timezone.utc)
    good = CanonicalCandle(
        source="test",
        symbol="BTCUSDT",
        event_time=now - timedelta(seconds=10),
        received_at=now,
        timeframe="1m",
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
        volume=1.5,
    )
    bad = CanonicalCandle(
        source="test",
        symbol="BTCUSDT",
        event_time=now - timedelta(seconds=5),
        received_at=now,
        timeframe="1m",
        open=100.0,
        high=90.0,  # high < low
        low=95.0,
        close=105.0,
        volume=-1.0,
    )
    future = CanonicalCandle(
        source="test",
        symbol="BTCUSDT",
        event_time=now + timedelta(hours=1),
        received_at=now,
        timeframe="1m",
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=1.0,
    )
    quote_bad = CanonicalQuote(
        source="test",
        symbol="ETHUSDT",
        event_time=now - timedelta(seconds=1),
        received_at=now,
        bid=2000.0,
        ask=1990.0,
    )
    with Session() as session:
        pipe = DataPipeline(session, provider=_Dummy(), settings=settings)
        result = pipe.process_many([good, bad, future, quote_bad])
        click.echo(
            json.dumps(
                {
                    "stored": result.stored,
                    "rejected": result.rejected,
                    "metrics": pipe.monitor.metrics.to_dict(),
                },
                indent=2,
            )
        )
