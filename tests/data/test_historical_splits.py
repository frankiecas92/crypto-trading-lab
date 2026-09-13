"""Dataset TRAIN/VAL/TEST lock — TEST locked; param selection cannot use TEST."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from crypto_lab.backtest.splits import ResearchPhase, ResearchSplit
from crypto_lab.data.database import get_session_factory, init_db, reset_engine
from crypto_lab.data.historical.catalog import DatasetSplitCatalog, research_split_for_candles
from crypto_lab.data.historical.constants import EVALUATE_OOS, QUALITY_VALID
from crypto_lab.data.historical.identity import compute_dataset_identity
from crypto_lab.data.historical.service import HistoricalDataService
from crypto_lab.data.models import HistoricalDataset
from crypto_lab.data.records import CanonicalCandle
from crypto_lab.exceptions import SplitLeakageError


T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _candles(n=20):
    out = []
    for i in range(n):
        et = T0 + timedelta(hours=i)
        out.append(
            CanonicalCandle(
                source="binance",
                symbol="ETHUSDT",
                source_symbol="ETHUSDT",
                event_time=et,
                received_at=et + timedelta(seconds=1),
                timeframe="1h",
                open=2000.0,
                high=2010.0,
                low=1990.0,
                close=2005.0,
                volume=1.0,
                base_asset="ETH",
                quote_asset="USDT",
                canonical_asset="ETH",
            )
        )
    return out


@pytest.fixture
def session(tmp_path):
    url = f"sqlite:///{tmp_path / 'split.db'}"
    reset_engine()
    init_db(url)
    Session = get_session_factory(url)
    with Session() as s:
        yield s


def _seed_dataset(session, candles):
    ident = compute_dataset_identity(
        candles, source="binance", source_symbol="ETHUSDT", timeframe="1h", git_commit=None
    )
    row = HistoricalDataset(
        dataset_id=ident.dataset_id,
        source=ident.source,
        source_symbol=ident.source_symbol,
        symbol=ident.source_symbol,
        base_asset=ident.base_asset,
        quote_asset=ident.quote_asset,
        canonical_asset=ident.canonical_asset,
        timeframe=ident.timeframe,
        start_time=ident.start,
        end_time=ident.end,
        record_count=ident.record_count,
        data_version=ident.data_version,
        git_commit=ident.git_commit,
        checksum=ident.checksum,
        quality_status=QUALITY_VALID,
        test_locked=True,
    )
    session.add(row)
    session.flush()
    return ident.dataset_id


def test_test_locked_by_default(session):
    candles = _candles(20)
    did = _seed_dataset(session, candles)
    cat = DatasetSplitCatalog(session)
    cat.attach_roles(did, candles)
    assert cat.is_test_locked(did) is True
    with pytest.raises(SplitLeakageError, match="locked"):
        cat.candles_for_role(did, "TEST", candles)


def test_param_selection_cannot_use_test(session):
    candles = _candles(20)
    did = _seed_dataset(session, candles)
    cat = DatasetSplitCatalog(session)
    cat.attach_roles(did, candles)
    with pytest.raises(SplitLeakageError, match="TEST"):
        cat.select_parameters(did, using_role="TEST")
    train = cat.select_parameters(did, using_role="TRAIN")
    assert len(train) > 0
    assert all(c.event_time < candles[12].event_time + timedelta(hours=1) for c in train)


def test_unlock_requires_evaluate_oos(session):
    candles = _candles(20)
    did = _seed_dataset(session, candles)
    cat = DatasetSplitCatalog(session)
    cat.attach_roles(did, candles)
    with pytest.raises(SplitLeakageError):
        cat.unlock_test(did, confirm="please")
    cat.set_phase(did, ResearchPhase.VALIDATION)
    cat.unlock_test(did, confirm=EVALUATE_OOS)
    assert cat.is_test_locked(did) is False
    test = cat.candles_for_role(did, "TEST", candles)
    assert len(test) > 0
    # TEST bars are the last slice — after train/val
    assert test[0].event_time >= train_end(candles)


def train_end(candles):
    # chronological 0.6 / 0.2 / 0.2
    n = len(candles)
    n_train = int(n * 0.6)
    n_val = int(n * 0.2)
    return candles[n_train + n_val].event_time


def test_research_split_api_unchanged():
    """Phase 3 ResearchSplit still locks TEST without EVALUATE_OOS."""
    rs = research_split_for_candles(_candles(30))
    assert isinstance(rs, ResearchSplit)
    assert rs.test_locked is True
    with pytest.raises(SplitLeakageError):
        rs.test_bars()
    rs.set_phase(ResearchPhase.EVALUATE_OOS)
    rs.unlock_test(confirm="EVALUATE_OOS")
    assert len(rs.test_bars()) > 0


def test_oos_param_change_still_rejected():
    rs = research_split_for_candles(_candles(30))
    rs.set_phase(ResearchPhase.EVALUATE_OOS)
    rs.unlock_test(confirm="EVALUATE_OOS", locked_parameters={"fast": 5})
    rs.test_bars(current_parameters={"fast": 5})
    with pytest.raises(SplitLeakageError, match="VIOLATION"):
        rs.test_bars(current_parameters={"fast": 99})
