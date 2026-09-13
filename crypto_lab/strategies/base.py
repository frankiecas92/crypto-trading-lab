"""Strategy ABC — generate_signals(data_view) only sees data available at T."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from crypto_lab.backtest.data_view import DataView
from crypto_lab.backtest.types import StrategySignal


class Strategy(ABC):
    """Common research strategy interface.

    Versioning: ``version()`` returns STRATEGY_v001 style ids. Implementations
    must bump the version when parameters or logic change; callers persist
    rows via ``strategy_versions`` and never silently overwrite.
    """

    strategy_id: str = "STRATEGY"
    _version_num: int = 1

    @abstractmethod
    def generate_signals(self, data_view: DataView) -> StrategySignal:
        """Return a signal using only ``data_view`` (no future bars)."""

    def parameters(self) -> Dict[str, Any]:
        return {}

    def version(self) -> str:
        return f"{self.strategy_id}_v{self._version_num:03d}"

    def metadata(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "version": self.version(),
            "parameters": self.parameters(),
        }

    def required_symbol(self) -> str | None:
        """If set, the engine ignores this strategy on other symbols."""
        return None
