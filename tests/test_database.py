"""Database schema tests."""

from crypto_lab.data.database import EXPECTED_TABLES, init_db, list_tables, schema_complete
from crypto_lab.data.models import MarketData, Portfolio, Position, Signal, StrategyVersion, SystemEvent, Trade
from crypto_lab.data.repositories import MarketDataRepository, PortfolioRepository
from datetime import datetime, timezone


def test_init_db_creates_all_tables(tmp_path):
    url = f"sqlite:///{tmp_path / 'schema.db'}"
    engine = init_db(url)
    tables = list_tables(engine)
    assert EXPECTED_TABLES.issubset(tables)
    assert schema_complete(engine)


def test_expected_table_names():
    assert EXPECTED_TABLES == {
        "market_data",
        "signals",
        "trades",
        "positions",
        "portfolio",
        "system_events",
        "strategy_versions",
    }


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

