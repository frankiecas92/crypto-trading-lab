"""CLI entrypoint: init-db, health, version — no exchange fetch commands."""

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
    """Crypto Trading Lab — Phase 1 infrastructure CLI."""
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
    """Create SQLite schema (all Phase 1 tables)."""
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


if __name__ == "__main__":
    main()

