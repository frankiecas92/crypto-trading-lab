"""Execution interfaces only — NO order placement in Phase 1."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from crypto_lab.execution.safety import guard_live_trading
from crypto_lab.exceptions import NotImplementedPhaseError


@dataclass
class OrderRequest:
    """Placeholder order request DTO (not executed in Phase 1)."""

    symbol: str
    side: str
    quantity: float
    price: Optional[float] = None
    order_type: str = "LIMIT"


@dataclass
class OrderResult:
    """Placeholder order result DTO."""

    success: bool
    message: str
    order_id: Optional[str] = None


class ExecutionGateway(ABC):
    """Abstract execution gateway — implementations in later phases."""

    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderResult:
        """Place an order (not implemented in Phase 1)."""


class BlockedLiveGateway(ExecutionGateway):
    """Any call raises SafetyError after guard — fails closed."""

    def place_order(self, request: OrderRequest) -> OrderResult:
        guard_live_trading()
        raise NotImplementedPhaseError(
            "Order placement is not available in Phase 1. "
            "Live and paper brokers ship in Phase 2+."
        )

