"""Strategy version persistence — never silently overwrite a prior version."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_lab.data.models import StrategyVersion
from crypto_lab.exceptions import ResearchError
from crypto_lab.strategies.base import Strategy


def register_strategy_version(
    session: Session,
    strategy: Strategy,
    *,
    description: str | None = None,
    set_active: bool = False,
) -> StrategyVersion:
    """Insert a strategy_versions row if new; refuse overwrite on conflict.

    Same (strategy_id, version) + same config_json → return existing.
    Same (strategy_id, version) + different config → ResearchError.
    """
    version = strategy.version()
    payload = json.dumps(strategy.parameters(), sort_keys=True)
    stmt = select(StrategyVersion).where(
        StrategyVersion.strategy_id == strategy.strategy_id,
        StrategyVersion.version == version,
    )
    existing = session.scalars(stmt).first()
    if existing is not None:
        if (existing.config_json or "") != payload:
            raise ResearchError(
                f"Refuse overwrite of {strategy.strategy_id} {version}: "
                f"stored config differs from current parameters"
            )
        return existing
    row = StrategyVersion(
        strategy_id=strategy.strategy_id,
        version=version,
        description=description or json.dumps(strategy.metadata()),
        config_json=payload,
        is_active=set_active,
    )
    session.add(row)
    session.flush()
    return row
