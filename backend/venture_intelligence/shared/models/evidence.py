"""Evidence chain (§17): observations, evidence, signals, hypotheses,
features (§8) and person classification (§16)."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from venture_intelligence.shared.models._helpers import _now, _uid, _utc
from venture_intelligence.shared.models.events import Source

# Classes and helpers defined here keep the historical public module path.
# OpenAPI component names (e.g. venture_intelligence__shared__models__SourceAccessMode
# on a name collision), pydantic core-schema refs and pickled qualified paths all
# derive from ``__module__``, which Python takes from ``__name__`` at definition time.
__name__ = "venture_intelligence.shared.models"

# ═══════════════════════════════════════════════════════════════════════════════
# §17  Evidence chain: Observation → Evidence → Signal → Hypothesis
# ═══════════════════════════════════════════════════════════════════════════════


class Observation(BaseModel):
    """
    A single raw fact extracted from an event (§8, §17).

    The smallest unit of evidence.  Multiple observations combine into
    Evidence; Evidence feeds Signal extraction.

    Example:
      fact  = "Created GitHub repo 'yc-f26-demo' on 2026-07-01"
      value = {"stars": 12, "language": "Python"}
    """

    observation_id: str = Field(default_factory=_uid)
    event_id: str  # FK → CanonicalEvent
    entity_id: str
    source: Source
    fact: str  # human-readable statement
    value: Any = None  # structured numeric/dict value
    observed_at: datetime = Field(default_factory=_now)
    confidence: float = 0.90


class Evidence(BaseModel):
    """
    A supported claim about an entity, backed by observations (§8, §17).

    Example:
      claim            = "Has active GitHub presence"
      observation_ids  = [obs_id_1, obs_id_2]
      confidence       = 0.88
    """

    evidence_id: str = Field(default_factory=_uid)
    entity_id: str
    claim: str
    observation_ids: list[str]
    confidence: float
    observed_at: datetime = Field(default_factory=_now)


class SignalType(str, Enum):
    """
    The seven signal types the Signal Worker extracts (§16).

    Each maps to one or more evidence claims and produces a normalized
    0.0–1.0 value representing signal strength.
    """

    REPEAT_FOUNDER = "repeat_founder"  # has prior company in knowledge graph
    HIRING_VELOCITY = "hiring_velocity"  # open roles / 30-day window
    TECHNICAL_DEPTH = "technical_depth"  # GitHub repos, commit frequency, languages
    RESEARCH_FOUNDER = "research_founder"  # published academic or technical papers
    STEALTH_STARTUP = "stealth_startup"  # LinkedIn updated but no public company yet
    PROGRAM_AFFINITY = "program_affinity"
    # Read compatibility for persisted pre-M6 signals and external consumers.
    # New computation and contracts use PROGRAM_AFFINITY.
    YC_LIKELIHOOD = "yc_likelihood"
    OPEN_SOURCE_LEADER = "open_source_leader"  # GitHub stars, forks, contributors
    HUMAN_FEEDBACK_POSITIVE = "human_feedback_positive"
    HUMAN_FEEDBACK_NEGATIVE = "human_feedback_negative"
    REALIZED_OUTCOME = "realized_outcome"


class Signal(BaseModel):
    """
    Normalized interpretation of one or more evidence items (§8, §16, §17).

    value is always in [0.0, 1.0]:
      0.0 = signal absent
      1.0 = signal at maximum observed strength
    """

    signal_id: str = Field(default_factory=_uid)
    schema_version: str = "4.0"
    signal_version: str = "m4-signal-1"
    entity_id: str
    signal_type: SignalType
    value: float  # 0.0 – 1.0 normalized strength
    evidence_ids: list[str]
    feature_ids: list[str] = Field(default_factory=list)
    crm_action_ids: list[str] = Field(default_factory=list)
    input_digest: str = ""
    explanation: str  # one-sentence human-readable rationale
    confidence: float
    observed_at: datetime = Field(default_factory=_now)


class Hypothesis(BaseModel):
    """
    Probabilistic inference produced by combining signals (§8, §17).

    Example:
      statement  = "Likely building AI Infrastructure tooling"
      probability = 0.78
    """

    hypothesis_id: str = Field(default_factory=_uid)
    schema_version: str = "4.0"
    hypothesis_version: str = "m4-hypothesis-1"
    entity_id: str
    statement: str
    signal_ids: list[str]
    input_digest: str = ""
    probability: float  # 0.0 – 1.0
    created_at: datetime = Field(default_factory=_now)


# ═══════════════════════════════════════════════════════════════════════════════
# §8  Feature — raw measurable value used by ranking (distinct from Signal)
# ═══════════════════════════════════════════════════════════════════════════════


class Feature(BaseModel):
    """
    A measurable raw value extracted from observations (§8).

    Features are the lowest-level numeric inputs to the ranking engine.
    They differ from Signals:
      Feature  = raw measurement  (e.g. github_stars = 142)
      Signal   = normalized 0–1 interpretation  (technical_depth = 0.63)

    Examples from §8:
      github_stars, research_papers, hiring_velocity,
      founder_count, community_centrality
    """

    feature_id: str = Field(default_factory=_uid)
    schema_version: str = "4.0"
    feature_version: str = "m4-feature-1"
    entity_id: str
    name: str  # e.g. "github_stars"
    value: float  # raw numeric value
    unit: str | None = None  # e.g. "count", "days", "USD"
    source: Source
    observed_at: datetime = Field(default_factory=_now)
    as_of: datetime | None = None
    computed_at: datetime | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    crm_action_ids: list[str] = Field(default_factory=list)
    formula: str = "legacy_direct_value"
    parameters: dict[str, Any] = Field(default_factory=dict)
    input_digest: str = ""
    program_id: str | None = None
    batch_id: str | None = None
    confidence: float = 0.90

    @field_validator("observed_at", "as_of", "computed_at")
    @classmethod
    def validate_feature_timestamp(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def normalize_feature_times(self) -> "Feature":
        if self.as_of is None:
            self.as_of = self.observed_at
        if self.computed_at is None:
            self.computed_at = self.as_of
        return self


# ═══════════════════════════════════════════════════════════════════════════════
# §16  Classification Worker output — person type labels
# ═══════════════════════════════════════════════════════════════════════════════


class PersonType(str, Enum):
    """
    Classification Worker output (§16).
    Determines how the ranking engine weights signals for this person.
    """

    FOUNDER = "founder"
    INVESTOR = "investor"
    RESEARCHER = "researcher"
    OPERATOR = "operator"
    UNKNOWN = "unknown"


class PersonClassification(BaseModel):
    """Result of the Classification Worker for one person entity."""

    classification_id: str = Field(default_factory=_uid)
    entity_id: str
    person_type: PersonType
    confidence: float
    evidence_ids: list[str] = Field(default_factory=list)
    classified_at: datetime = Field(default_factory=_now)
