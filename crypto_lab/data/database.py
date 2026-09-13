"""Database engine, session factory, init_db, and Phase 2 additive migrate helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from crypto_lab.data.models import Base
from crypto_lab.exceptions import DatabaseError

EXPECTED_TABLES = frozenset(
    {
        "market_data",
        "signals",
        "trades",
        "positions",
        "portfolio",
        "system_events",
        "strategy_versions",
        # Phase 2
        "market_trades",
        "market_quotes",
        "data_quality_events",
    }
)

PHASE1_TABLES = frozenset(
    {
        "market_data",
        "signals",
        "trades",
        "positions",
        "portfolio",
        "system_events",
        "strategy_versions",
    }
)

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker[Session]] = None


def _ensure_sqlite_parent(url: str) -> None:
    """Create parent directory for file-based SQLite URLs."""
    if not url.startswith("sqlite:///"):
        return
    path_part = url.replace("sqlite:///", "", 1)
    if path_part == ":memory:" or path_part.startswith(":memory:"):
        return
    path = Path(path_part)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)


def get_engine(database_url: str | None = None, *, echo: bool = False) -> Engine:
    """Create or return a SQLAlchemy engine."""
    global _engine, _SessionLocal
    if database_url is None:
        from crypto_lab.config import get_settings

        database_url = get_settings().database_url

    if _engine is not None and database_url == str(_engine.url):
        return _engine

    try:
        _ensure_sqlite_parent(database_url)
        connect_args = {}
        if database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_engine(database_url, echo=echo, future=True, connect_args=connect_args)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
        return _engine
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError(f"Failed to create database engine: {exc}") from exc


def get_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    """Return a session factory bound to the engine."""
    global _SessionLocal
    get_engine(database_url)
    if _SessionLocal is None:
        raise DatabaseError("Session factory not initialized")
    return _SessionLocal


def reset_engine() -> None:
    """Clear cached engine/session (for tests)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def init_db(database_url: str | None = None) -> Engine:
    """Create all tables (create_all) and apply additive Phase 2 migrations."""
    engine = get_engine(database_url)
    try:
        Base.metadata.create_all(bind=engine)
        apply_phase2_migrations(engine)
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError(f"Failed to initialize schema: {exc}") from exc
    return engine


def list_tables(engine: Engine | None = None) -> set[str]:
    """Return set of table names present in the database."""
    eng = engine or get_engine()
    return set(inspect(eng).get_table_names())


def schema_complete(engine: Engine | None = None) -> bool:
    """True if all expected Phase 1+2 tables exist."""
    return EXPECTED_TABLES.issubset(list_tables(engine))


def _column_names(engine: Engine, table: str) -> set[str]:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def apply_phase2_migrations(engine: Engine) -> list[str]:
    """Additive SQLite-safe migrations for Phase 2. Idempotent. Returns actions taken."""
    actions: list[str] = []
    dialect = engine.dialect.name

    with engine.begin() as conn:
        cols = set()
        insp = inspect(engine)
        if "market_data" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("market_data")}

        if "market_data" in insp.get_table_names():
            if "event_time" not in cols:
                conn.execute(text("ALTER TABLE market_data ADD COLUMN event_time DATETIME"))
                actions.append("add market_data.event_time")
            if "received_at" not in cols:
                conn.execute(text("ALTER TABLE market_data ADD COLUMN received_at DATETIME"))
                actions.append("add market_data.received_at")
            for col, decl in (
                ("source_symbol", "VARCHAR(32)"),
                ("base_asset", "VARCHAR(16)"),
                ("quote_asset", "VARCHAR(16)"),
                ("canonical_asset", "VARCHAR(16)"),
            ):
                if col not in cols:
                    conn.execute(text(f"ALTER TABLE market_data ADD COLUMN {col} {decl}"))
                    actions.append(f"add market_data.{col}")

            # Backfill event_time / received_at from legacy columns when null
            conn.execute(
                text(
                    "UPDATE market_data SET event_time = ts "
                    "WHERE event_time IS NULL AND ts IS NOT NULL"
                )
            )
            conn.execute(
                text(
                    "UPDATE market_data SET received_at = created_at "
                    "WHERE received_at IS NULL AND created_at IS NOT NULL"
                )
            )
            actions.append("backfill market_data event_time/received_at")

            # Rebuild unique constraint to include source (SQLite table rebuild)
            if dialect == "sqlite":
                # Refresh inspector so newly ALTERed identity columns are visible
                insp = inspect(engine)
                rebuilt = _maybe_rebuild_market_data_unique(conn, insp)
                if rebuilt:
                    actions.append("rebuild market_data unique -> uq_market_data_src_ts")
                    # Re-read columns after rebuild and ensure identity cols exist
                    insp = inspect(engine)
                    cols = {c["name"] for c in insp.get_columns("market_data")}
                    for col, decl in (
                        ("source_symbol", "VARCHAR(32)"),
                        ("base_asset", "VARCHAR(16)"),
                        ("quote_asset", "VARCHAR(16)"),
                        ("canonical_asset", "VARCHAR(16)"),
                    ):
                        if col not in cols:
                            conn.execute(text(f"ALTER TABLE market_data ADD COLUMN {col} {decl}"))
                            actions.append(f"add market_data.{col} post-rebuild")

        # Additive identity columns on market_trades / market_quotes
        insp = inspect(engine)
        for table in ("market_trades", "market_quotes"):
            if table not in insp.get_table_names():
                continue
            tcols = {c["name"] for c in insp.get_columns(table)}
            for col, decl in (
                ("source_symbol", "VARCHAR(32)"),
                ("base_asset", "VARCHAR(16)"),
                ("quote_asset", "VARCHAR(16)"),
                ("canonical_asset", "VARCHAR(16)"),
            ):
                if col not in tcols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {decl}"))
                    actions.append(f"add {table}.{col}")

    # Ensure new tables exist (create_all should have done this; re-check)
    Base.metadata.create_all(bind=engine)
    return actions


def _maybe_rebuild_market_data_unique(conn, insp) -> bool:
    """If legacy uq_market_data (no source) exists, rebuild table with source in unique.

    Preserves all rows. No-op if already migrated or fresh Phase 2 schema.
    """
    try:
        uks = insp.get_unique_constraints("market_data")
    except Exception:  # noqa: BLE001
        return False

    has_legacy = any(u.get("name") == "uq_market_data" for u in uks)
    has_new = any(u.get("name") == "uq_market_data_src_ts" for u in uks)
    # SQLite may report unnamed uniques; also check column sets
    for u in uks:
        cols = list(u.get("column_names") or [])
        if cols == ["source", "symbol", "ts", "timeframe"]:
            has_new = True
        if cols == ["symbol", "ts", "timeframe"]:
            has_legacy = True

    if has_new and not has_legacy:
        return False
    if not has_legacy and has_new:
        return False
    if not has_legacy and not has_new:
        # Fresh create_all already used new unique via SQLAlchemy
        return False

    # Rebuild
    conn.execute(text("PRAGMA foreign_keys=OFF"))
    conn.execute(
        text(
            """
            CREATE TABLE market_data_new (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                symbol VARCHAR(32) NOT NULL,
                timeframe VARCHAR(16) NOT NULL,
                ts DATETIME NOT NULL,
                open FLOAT NOT NULL,
                high FLOAT NOT NULL,
                low FLOAT NOT NULL,
                close FLOAT NOT NULL,
                volume FLOAT NOT NULL,
                source VARCHAR(64) NOT NULL,
                created_at DATETIME,
                event_time DATETIME,
                received_at DATETIME,
                source_symbol VARCHAR(32),
                base_asset VARCHAR(16),
                quote_asset VARCHAR(16),
                canonical_asset VARCHAR(16),
                CONSTRAINT uq_market_data_src_ts UNIQUE (source, symbol, ts, timeframe)
            )
            """
        )
    )
    src_cols = {c["name"] for c in insp.get_columns("market_data")}
    id_cols = [
        c
        for c in ("source_symbol", "base_asset", "quote_asset", "canonical_asset")
        if c in src_cols
    ]
    extra_insert = (", " + ", ".join(id_cols)) if id_cols else ""
    extra_select = (", " + ", ".join(id_cols)) if id_cols else ""
    conn.execute(
        text(
            f"""
            INSERT INTO market_data_new (
                id, symbol, timeframe, ts, open, high, low, close, volume,
                source, created_at, event_time, received_at{extra_insert}
            )
            SELECT
                id, symbol, timeframe, ts, open, high, low, close, volume,
                source, created_at,
                COALESCE(event_time, ts),
                COALESCE(received_at, created_at){extra_select}
            FROM market_data
            """
        )
    )
    conn.execute(text("DROP TABLE market_data"))
    conn.execute(text("ALTER TABLE market_data_new RENAME TO market_data"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_market_data_symbol ON market_data (symbol)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_market_data_ts ON market_data (ts)"))
    conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_market_data_event_time ON market_data (event_time)")
    )
    conn.execute(text("PRAGMA foreign_keys=ON"))
    return True
