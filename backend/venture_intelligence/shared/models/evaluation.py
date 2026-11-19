"""Post-batch evaluation result schema (§21)."""

from datetime import datetime

from pydantic import BaseModel, Field

from venture_intelligence.shared.models._helpers import _now

# Classes and helpers defined here keep the historical public module path.
# OpenAPI component names (e.g. venture_intelligence__shared__models__SourceAccessMode
# on a name collision), pydantic core-schema refs and pickled qualified paths all
# derive from ``__module__``, which Python takes from ``__name__`` at definition time.
__name__ = "venture_intelligence.shared.models"

# ═══════════════════════════════════════════════════════════════════════════════
# §21  Evaluation framework
# ═══════════════════════════════════════════════════════════════════════════════


class EvaluationResult(BaseModel):
    """
    Post-batch evaluation metrics (§21).

    Computed after each YC batch is fully announced (Demo Day + n weeks).
    Used to tune weights in DIMENSION_WEIGHTS and alert thresholds.
    """

    batch: str
    evaluated_at: datetime = Field(default_factory=_now)

    # Ranking quality
    precision_at_10: float  # % of top-10 alerts that were real YC companies
    precision_at_25: float  # same for top-25
    recall: float  # % of actual YC companies MFC detected early

    # Lead time (the core competitive advantage)
    avg_lead_time_days: float  # days before official YC announcement
    median_lead_time_days: float

    # Error analysis
    false_positives: int  # alerts on non-YC companies
    missed_companies: int  # YC companies we never detected

    # Business metrics
    alert_acceptance_rate: float  # % of CONTACT alerts acted on by partner
    investment_conversion_rate: float  # % of CONTACT alerts → investment

    total_entities_ranked: int
    notes: str = ""
