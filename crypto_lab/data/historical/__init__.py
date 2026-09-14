"""Phase 4A historical dataset pipeline.

Public REST history only. Not trading evidence.
Never EDGE_CONFIRMED / PROFITABLE. MODE=PAPER / LIVE_TRADING=False.
"""

from crypto_lab.data.historical.catalog import DatasetSplitCatalog
from crypto_lab.data.historical.downloader import HistoricalDownloader, is_candle_closed
from crypto_lab.data.historical.identity import DatasetIdentity, compute_dataset_identity
from crypto_lab.data.historical.quality import DatasetQualityReport, evaluate_dataset_quality
from crypto_lab.data.historical.resample import resample_causal
from crypto_lab.data.historical.service import HistoricalDataService
from crypto_lab.data.historical.snapshots import create_snapshot

__all__ = [
    "DatasetIdentity",
    "DatasetQualityReport",
    "DatasetSplitCatalog",
    "HistoricalDataService",
    "HistoricalDownloader",
    "is_candle_closed",
    "compute_dataset_identity",
    "create_snapshot",
    "evaluate_dataset_quality",
    "resample_causal",
]
