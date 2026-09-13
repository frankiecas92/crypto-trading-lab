"""Database schema tests."""

from datetime import datetime, timezone

from crypto_lab.data.database import EXPECTED_TABLES, PHASE1_TABLES, init_db, list_tables, schema_complete
from crypto_lab.data.models import MarketData, Portfolio
from crypto_lab.data.repositories import MarketDataRepository, PortfolioRepository


def test_init_db_creates_all_tables(tmp_path):
    url = f"sqlite:///{tmp_path / 'schema.db'}"
    engine = init_db(url)
    tables = list_tables(engine)
    assert EXPECTED_TABLES.issubset(tables)
    assert schema_complete(engine)


def test_expected_table_names():
    assert PHASE1_TABLES == {
        "market_data",
        "signals",
        "trades",
        "positions",
        "portfolio",
        "system_events",
        "strategy_versions",
    }
    assert "market_trades" in EXPECTED_TABLES
    assert "market_quotes" in EXPECTED_TABLES
    assert "data_quality_events" in EXPECTED_TABLES


def test_repository_crud_stub(tmp_path):
    from crypto_lab.data.database import get_session_factory, reset_engine

    url = f"sqlite:///{tmp_path / 'repo.db'}"
    reset_engine()
    init_db(url)
    Session = get_session_factory(url)
    with Session() as session:
        repo = PortfolioRepository(session)
        p = Portfolio(name="test", cash=1000.0, equity=1000.0)
        repo.add(p)
        session.commit()
        loaded = repo.get(p.id)
        assert loaded is not None
        assert loaded.name == "test"
        assert loaded.cash == 1000.0


def test_market_data_event_received_columns(tmp_path):
    from crypto_lab.data.database import get_session_factory, reset_engine

    url = f"sqlite:///{tmp_path / 'md.db'}"
    reset_engine()
    init_db(url)
    Session = get_session_factory(url)
    now = datetime.now(timezone.utc)
    with Session() as session:
        repo = MarketDataRepository(session)
        row = MarketData(
            symbol="BTCUSDT",
            timeframe="1m",
            ts=now,
            open=1.0,
            high=2.0,
            low=0.5,
            close=1.5,
            volume=10.0,
            source="binance",
            event_time=now,
            received_at=now,
        )
        repo.add(row)
        session.commit()
        loaded = repo.get(row.id)
        assert loaded.event_time is not None
        assert loaded.received_at is not None

    # Additive identity columns present (nullable)
    from crypto_lab.data.database import _column_names
    from crypto_lab.data.database import get_engine

    cols = _column_names(get_engine(url), "market_data")
    for name in ("source_symbol", "base_asset", "quote_asset", "canonical_asset"):
        assert name in cols
    for table in ("market_trades", "market_quotes"):
        tcols = _column_names(get_engine(url), table)
        for name in ("source_symbol", "base_asset", "quote_asset", "canonical_asset"):
            assert name in tcols
