"""CLI entrypoint: init-db, health, version, data, experiment demo, historical-data."""

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
    """Crypto Trading Lab — Phase 4B Research Campaign (paper / no live trading)."""
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
    """Create SQLite schema (Phase 1–4B tables) and apply additive migrations."""
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



@main.group("experiment")
def experiment_grp() -> None:
    """Phase 3 strategy research (historical simulation only — no live loop)."""


@experiment_grp.command("demo")
@click.option("--out", "out_dir", default="data/experiments/demo", show_default=True)
@click.option("--database-url", default=None, help="Override DATABASE_URL for registry persist")
@click.option("--bars", default=180, show_default=True, type=int)
@click.option("--seed", default=7, show_default=True, type=int)
@click.option("--cached", is_flag=True, default=False, help="Prefer cached OHLCV from Data Engine if present")
@click.option("--no-persist", is_flag=True, default=False, help="Skip SQLite experiment rows")
def experiment_demo(
    out_dir: str,
    database_url: str | None,
    bars: int,
    seed: int,
    cached: bool,
    no_persist: bool,
) -> None:
    """Run the 4 benchmark controls on synthetic (or cached) OHLCV; write IS/OOS/WF/robustness/MC."""
    from crypto_lab.backtest.demo import run_demo
    from crypto_lab.execution.safety import guard_live_trading

    guard_live_trading()
    summary = run_demo(
        out_dir=out_dir,
        database_url=database_url,
        n=bars,
        seed=seed,
        prefer_cached=cached,
        persist=not no_persist,
    )
    click.echo(
        json.dumps(
            {
                "out": out_dir,
                "overfitting_risk": summary.get("overfitting_risk"),
                "scientific_conclusion": summary.get("scientific_conclusion"),
                "symbols": list(summary.get("symbols", {})),
                "costs": summary.get("cost_assumptions"),
            },
            indent=2,
        )
    )


def _parse_iso(value: str | None):
    if not value:
        return None
    from datetime import datetime, timezone

    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@main.group("historical-data")
def historical_data_grp() -> None:
    """Phase 4A historical dataset pipeline (public REST, small safe defaults).

    Not trading evidence. Never EDGE_CONFIRMED / PROFITABLE. TEST stays locked.
    """


@historical_data_grp.command("download")
@click.option("--source", default="binance", show_default=True, help="binance | coinbase")
@click.option("--symbol", default="BTCUSDT", show_default=True)
@click.option("--timeframe", default="1h", show_default=True)
@click.option("--start", default=None, help="UTC ISO start (default: now-3d)")
@click.option("--end", default=None, help="UTC ISO end (default: now)")
@click.option("--max-bars", default=200, show_default=True, type=int, help="Small cap; huge ranges refused")
@click.option("--no-resume", is_flag=True, default=False, help="Re-fetch even if candles exist")
@click.option("--database-url", default=None)
def historical_download(
    source: str,
    symbol: str,
    timeframe: str,
    start: str | None,
    end: str | None,
    max_bars: int,
    no_resume: bool,
    database_url: str | None,
) -> None:
    """Download a small public OHLCV range, validate, store, and register a dataset."""
    from crypto_lab.data.database import get_session_factory, init_db, reset_engine
    from crypto_lab.data.historical.service import HistoricalDataService
    from crypto_lab.execution.safety import guard_live_trading

    guard_live_trading()
    settings = get_settings()
    url = database_url or settings.database_url
    reset_engine()
    init_db(url)
    Session = get_session_factory(url)
    with Session() as session:
        svc = HistoricalDataService(session, settings=settings, source=source)
        try:
            payload = svc.download(
                symbol,
                timeframe=timeframe,
                start=_parse_iso(start),
                end=_parse_iso(end),
                max_bars=max_bars,
                resume=not no_resume,
            )
            click.echo(json.dumps(payload, indent=2, default=str))
        finally:
            svc.close()


@historical_data_grp.command("validate")
@click.option("--dataset-id", default=None)
@click.option("--source", default=None)
@click.option("--symbol", default=None)
@click.option("--timeframe", default="1h", show_default=True)
@click.option("--start", default=None)
@click.option("--end", default=None)
@click.option("--database-url", default=None)
def historical_validate(
    dataset_id: str | None,
    source: str | None,
    symbol: str | None,
    timeframe: str,
    start: str | None,
    end: str | None,
    database_url: str | None,
) -> None:
    """Validate a registered dataset or a stored source/symbol/time range."""
    from crypto_lab.data.database import get_session_factory, init_db, reset_engine
    from crypto_lab.data.historical.service import HistoricalDataService
    from crypto_lab.execution.safety import guard_live_trading

    guard_live_trading()
    settings = get_settings()
    url = database_url or settings.database_url
    reset_engine()
    init_db(url)
    Session = get_session_factory(url)
    with Session() as session:
        svc = HistoricalDataService(session, settings=settings, source=source or "binance")
        try:
            payload = svc.validate(
                dataset_id=dataset_id,
                source=source,
                symbol=symbol,
                timeframe=timeframe,
                start=_parse_iso(start),
                end=_parse_iso(end),
            )
            click.echo(json.dumps(payload, indent=2, default=str))
        finally:
            svc.close()


@historical_data_grp.command("status")
@click.option("--dataset-id", default=None)
@click.option("--database-url", default=None)
def historical_status(dataset_id: str | None, database_url: str | None) -> None:
    """Show catalog status (quality, gaps, TEST lock). No profitability claims."""
    from crypto_lab.data.database import get_session_factory, init_db, reset_engine
    from crypto_lab.data.historical.service import HistoricalDataService
    from crypto_lab.execution.safety import guard_live_trading

    guard_live_trading()
    settings = get_settings()
    url = database_url or settings.database_url
    reset_engine()
    init_db(url)
    Session = get_session_factory(url)
    with Session() as session:
        svc = HistoricalDataService(session, settings=settings)
        try:
            payload = svc.status(dataset_id)
            click.echo(json.dumps(payload, indent=2, default=str))
        finally:
            svc.close()


@historical_data_grp.command("snapshot")
@click.option("--dataset-id", required=True)
@click.option("--database-url", default=None)
def historical_snapshot(dataset_id: str, database_url: str | None) -> None:
    """Write a reproducible snapshot of the exact dataset (id/version/checksum)."""
    from crypto_lab.data.database import get_session_factory, init_db, reset_engine
    from crypto_lab.data.historical.service import HistoricalDataService
    from crypto_lab.execution.safety import guard_live_trading

    guard_live_trading()
    settings = get_settings()
    url = database_url or settings.database_url
    reset_engine()
    init_db(url)
    Session = get_session_factory(url)
    with Session() as session:
        svc = HistoricalDataService(session, settings=settings)
        try:
            payload = svc.snapshot(dataset_id)
            click.echo(json.dumps(payload, indent=2, default=str))
        finally:
            svc.close()


@main.group("research-campaign")
def research_campaign_grp() -> None:
    """Phase 4B research campaign / strategy evaluation (historical only — no live).

    Strict TRAIN/VAL/TEST, param selection on VAL only, OOS after lock,
    WF / costs / robustness / MC. Never EDGE_CONFIRMED on thin samples.
    """


@research_campaign_grp.command("run")
@click.option("--out", "out_dir", default="data/experiments/campaign", show_default=True)
@click.option("--database-url", default=None, help="Override DATABASE_URL")
@click.option("--dataset-id", default=None, help="Phase 4A catalog dataset_id (preferred)")
@click.option("--symbol", "symbols", multiple=True, help="Symbol (repeatable; default BTC+ETH)")
@click.option("--timeframe", default="1h", show_default=True)
@click.option("--seed", default=7, show_default=True, type=int)
@click.option("--max-bars", default=120, show_default=True, type=int, help="Small safe window (or real-campaign size with --real)")
@click.option("--fixture", is_flag=True, default=False, help="Use synthetic fixtures (no network)")
@click.option("--allow-download", is_flag=True, default=False, help="If catalog empty, download small public window")
@click.option("--no-persist", is_flag=True, default=False, help="Skip SQLite experiment rows")
@click.option("--cost-profile", default=None, help="BASE / CONSERVATIVE / STRESS")
@click.option("--fixture-bars", default=120, show_default=True, type=int)
@click.option("--real", is_flag=True, default=False, help="Phase 4B-REAL: real Binance history (elevated hard max)")
@click.option("--years", default=2.0, show_default=True, type=float, help="Years of history for --real")
def research_campaign_run(
    out_dir: str,
    database_url: str | None,
    dataset_id: str | None,
    symbols: tuple[str, ...],
    timeframe: str,
    seed: int,
    max_bars: int,
    fixture: bool,
    allow_download: bool,
    no_persist: bool,
    cost_profile: str | None,
    fixture_bars: int,
    real: bool,
    years: float,
) -> None:
    """Run Phase 4B research campaign protocol (safe defaults / small range).

    Use --real (or run-real) for Phase 4B-REAL on public Binance Spot history.
    """
    from crypto_lab.execution.safety import guard_live_trading

    guard_live_trading()
    if real and fixture:
        raise click.ClickException("--real and --fixture are mutually exclusive")
    if real:
        from crypto_lab.research.campaign import run_real_research_campaign

        # Casual CLI default max-bars=120; for --real bump to research-sized window
        real_max = max_bars if max_bars and max_bars > 120 else 20000
        summary = run_real_research_campaign(
            out_dir=out_dir if out_dir != "data/experiments/campaign" else "data/experiments/campaign_real",
            database_url=database_url,
            symbols=list(symbols) if symbols else None,
            timeframe=timeframe,
            years=years,
            max_bars=real_max,
            seed=seed,
            persist=not no_persist,
            cost_profile=cost_profile,
        )
    else:
        from crypto_lab.research.campaign import run_research_campaign

        summary = run_research_campaign(
            out_dir=out_dir,
            database_url=database_url,
            dataset_id=dataset_id,
            symbols=list(symbols) if symbols else None,
            timeframe=timeframe,
            seed=seed,
            max_bars=max_bars,
            persist=not no_persist,
            use_fixture=fixture,
            allow_download=allow_download,
            cost_profile=cost_profile,
            n_fixture=fixture_bars,
        )
    evidence = summary.get("evidence") or {}
    click.echo(
        json.dumps(
            {
                "phase": summary.get("phase", "4B"),
                "real_historical": bool(summary.get("real_historical")),
                "out": out_dir,
                "EVIDENCE_STATUS": summary.get("EVIDENCE_STATUS"),
                "scientific_conclusion": summary.get("scientific_conclusion"),
                "EDGE_CONFIRMED": False,
                "PROFITABLE": False,
                "PHASE5_JUSTIFIED": summary.get("PHASE5_JUSTIFIED", False),
                "consider_phase5": summary.get("consider_phase5"),
                "overfitting_risk": summary.get("overfitting_risk"),
                "symbols": list(summary.get("symbols", {})),
                "datasets": {
                    s: (b.get("dataset_id") if isinstance(b, dict) else None)
                    for s, b in (summary.get("symbols") or {}).items()
                },
                "cost_profile": summary.get("cost_profile"),
                "git_commit": summary.get("git_commit"),
                "why": evidence.get("why", [])[:5],
                "MODE": summary.get("MODE"),
                "LIVE_TRADING": summary.get("LIVE_TRADING"),
            },
            indent=2,
            default=str,
        )
    )


@research_campaign_grp.command("run-real")
@click.option("--out", "out_dir", default="data/experiments/campaign_real", show_default=True)
@click.option("--database-url", default=None, help="Override DATABASE_URL")
@click.option("--symbol", "symbols", multiple=True, help="Symbol (repeatable; default BTC+ETH)")
@click.option("--timeframe", default="1h", show_default=True)
@click.option("--years", default=2.0, show_default=True, type=float)
@click.option("--max-bars", default=20000, show_default=True, type=int)
@click.option("--seed", default=7, show_default=True, type=int)
@click.option("--no-persist", is_flag=True, default=False)
@click.option("--cost-profile", default=None, help="BASE / CONSERVATIVE / STRESS")
@click.option("--catalog-only", is_flag=True, default=False, help="Prefer existing catalog; download if missing")
def research_campaign_run_real(
    out_dir: str,
    database_url: str | None,
    symbols: tuple[str, ...],
    timeframe: str,
    years: float,
    max_bars: int,
    seed: int,
    no_persist: bool,
    cost_profile: str | None,
    catalog_only: bool,
) -> None:
    """Phase 4B-REAL: real Binance Spot historical evaluation (elevated hard max)."""
    from crypto_lab.execution.safety import guard_live_trading
    from crypto_lab.research.campaign import run_real_research_campaign

    guard_live_trading()
    summary = run_real_research_campaign(
        out_dir=out_dir,
        database_url=database_url,
        symbols=list(symbols) if symbols else None,
        timeframe=timeframe,
        years=years,
        max_bars=max_bars,
        seed=seed,
        persist=not no_persist,
        cost_profile=cost_profile,
        allow_catalog_only=catalog_only,
    )
    evidence = summary.get("evidence") or {}
    click.echo(
        json.dumps(
            {
                "phase": "4B-REAL",
                "real_historical": True,
                "out": out_dir,
                "EVIDENCE_STATUS": summary.get("EVIDENCE_STATUS"),
                "scientific_conclusion": summary.get("scientific_conclusion"),
                "EDGE_CONFIRMED": False,
                "PROFITABLE": False,
                "PHASE5_JUSTIFIED": False,
                "consider_phase5": summary.get("consider_phase5"),
                "download_log": summary.get("download_log"),
                "symbols": list(summary.get("symbols", {})),
                "datasets": {
                    s: (b.get("dataset_id") if isinstance(b, dict) else None)
                    for s, b in (summary.get("symbols") or {}).items()
                },
                "registry_counters": summary.get("registry_counters"),
                "git_commit": summary.get("git_commit"),
                "why": evidence.get("why", [])[:8],
                "MODE": summary.get("MODE"),
                "LIVE_TRADING": summary.get("LIVE_TRADING"),
            },
            indent=2,
            default=str,
        )
    )

