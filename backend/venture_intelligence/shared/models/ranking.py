"""Founder ranking engine models and dimension weights (§18)."""

from datetime import datetime

from pydantic import BaseModel, Field

from venture_intelligence.shared.models._helpers import _now, _uid

# Classes and helpers defined here keep the historical public module path.
# OpenAPI component names (e.g. venture_intelligence__shared__models__SourceAccessMode
# on a name collision), pydantic core-schema refs and pickled qualified paths all
# derive from ``__module__``, which Python takes from ``__name__`` at definition time.
__name__ = "venture_intelligence.shared.models"

# ═══════════════════════════════════════════════════════════════════════════════
# §18  Founder Ranking Engine
# ═══════════════════════════════════════════════════════════════════════════════

# The nine dimensions from §18, with their default contribution weights.
# Weights sum to 1.0.  Adjustable via config or partner feedback.
DIMENSION_WEIGHTS: dict[str, float] = {
    "technical_depth": 0.20,  # GitHub, open source, patents
    "research_output": 0.12,  # papers, citations, h-index signals
    "hiring_velocity": 0.12,  # open roles per 30 days
    "network_quality": 0.10,  # connections to known strong founders/investors
    "founder_experience": 0.15,  # prior exits, repeat founder, domain tenure
    "product_momentum": 0.12,  # commits, releases, PH upvotes
    "community_reputation": 0.08,  # HN karma, Twitter/X following, blog reach
    "strategic_fit": 0.06,  # sector alignment with MFC thesis
    "timing": 0.05,  # market tailwinds, regulatory window
}


class RankingDimension(BaseModel):
    """One axis of the nine-dimension ranking (§18)."""

    name: str  # must be one of DIMENSION_WEIGHTS keys
    score: float  # 0.0 – 1.0
    weight: float  # copied from DIMENSION_WEIGHTS (may be partner-tuned)
    evidence: str  # one-sentence explanation of why this score was assigned
    signal_ids: list[str] = Field(default_factory=list)
    feature_ids: list[str] = Field(default_factory=list)


class FounderRanking(BaseModel):
    """
    Complete ranked output for a single entity (§18).

    composite_score = Σ(dimension.score × dimension.weight)
    rank is global across all tracked entities — lower is better.
    """

    ranking_id: str = Field(default_factory=_uid)
    schema_version: str = "4.0"
    ranking_version: str = "m4-ranking-1"
    entity_id: str
    rank: int
    composite_score: float
    dimensions: list[RankingDimension]
    hypothesis_ids: list[str]
    evidence_ids: list[str]
    signal_ids: list[str] = Field(default_factory=list)
    feature_ids: list[str] = Field(default_factory=list)
    crm_action_ids: list[str] = Field(default_factory=list)
    input_digest: str = ""
    reasoning: str  # 2–3 sentence human-readable summary
    confidence: float
    ranked_at: datetime = Field(default_factory=_now)
