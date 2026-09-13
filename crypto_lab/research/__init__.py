"""Phase 4B — Research campaign / strategy evaluation protocol.

Orchestrates historical strategy evaluation on Phase 4A datasets using
Phase 3 engines. Never emits EDGE_CONFIRMED or PROFITABLE on thin samples.
MODE=PAPER / LIVE_TRADING=False remain mandatory.
"""

from crypto_lab.research.campaign import (
    ResearchCampaign,
    run_real_research_campaign,
    run_research_campaign,
)

__all__ = ["ResearchCampaign", "run_research_campaign", "run_real_research_campaign"]
