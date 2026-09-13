"""Database engine, session factory, and init_db helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, inspect
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
    }
)

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker[Session]] = None


def _ensure_sqlite_parent(url: str) -> None:
    """Create parent directory for file-based SQLite URLs."""
    if not url.startswith("sqlite:///"):
        return
    # sqlite:///./data/lab.db or sqlite:////abs/path
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
    """Create all tables (create_all). Returns the engine."""
    engine = get_engine(database_url)
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError(f"Failed to initialize schema: {exc}") from exc
    return engine


def list_tables(engine: Engine | None = None) -> set[str]:
    """Return set of table names present in the database."""
    eng = engine or get_engine()
    return set(inspect(eng).get_table_names())


def schema_complete(engine: Engine | None = None) -> bool:
    """True if all expected Phase 1 tables exist."""
    return EXPECTED_TABLES.issubset(list_tables(engine))

