"""
shared/models — complete domain model for the Venture Intelligence OS.

Every concept referenced in the design doc has a corresponding Pydantic
model in this package.  Nothing downstream invents its own types; everything
imports from ``venture_intelligence.shared.models``, which re-exports every
name from the domain modules below (explicit ``X as X`` re-exports, no
``__all__``, so the namespace matches the former single-file module).

Modules:
  events      §12 canonical event schema, §13 EventType taxonomy, sources
  evidence    §8/§16/§17 Observation, Evidence, Signal, Hypothesis, Feature,
              person classification
  ranking     §18 nine ranking dimensions and their weights
  alerts      §8/§19 Recommendation, Alert and lifecycle events
  entities    §14 Person and Company
  graph       §15 knowledge graph node and edge types
  crm         §20 CRM stages, valid transitions, partner-action events,
              investigations
  evaluation  §21 evaluation result schema
  research    bounded public research contracts
"""

from datetime import (
    datetime as datetime,
)
from datetime import (
    timezone as timezone,
)
from enum import (
    Enum as Enum,
)
from typing import (
    Any as Any,
)
from uuid import (
    uuid4 as uuid4,
)

from pydantic import (
    BaseModel as BaseModel,
)
from pydantic import (
    Field as Field,
)
from pydantic import (
    field_validator as field_validator,
)
from pydantic import (
    model_validator as model_validator,
)

from venture_intelligence.shared.models._helpers import (
    _now as _now,
)
from venture_intelligence.shared.models._helpers import (
    _uid as _uid,
)
from venture_intelligence.shared.models._helpers import (
    _utc as _utc,
)
from venture_intelligence.shared.models.alerts import (
    Alert as Alert,
)
from venture_intelligence.shared.models.alerts import (
    AlertLifecycleEvent as AlertLifecycleEvent,
)
from venture_intelligence.shared.models.alerts import (
    AlertLifecycleStatus as AlertLifecycleStatus,
)
from venture_intelligence.shared.models.alerts import (
    Recommendation as Recommendation,
)
from venture_intelligence.shared.models.alerts import (
    RecommendationAction as RecommendationAction,
)
from venture_intelligence.shared.models.alerts import (
    RecommendationLifecycleEvent as RecommendationLifecycleEvent,
)
from venture_intelligence.shared.models.alerts import (
    RecommendationLifecycleStatus as RecommendationLifecycleStatus,
)
from venture_intelligence.shared.models.crm import (
    CRM_TRANSITIONS as CRM_TRANSITIONS,
)
from venture_intelligence.shared.models.crm import (
    CRMActionType as CRMActionType,
)
from venture_intelligence.shared.models.crm import (
    CRMEvent as CRMEvent,
)
from venture_intelligence.shared.models.crm import (
    CRMFeedbackKind as CRMFeedbackKind,
)
from venture_intelligence.shared.models.crm import (
    CRMRecord as CRMRecord,
)
from venture_intelligence.shared.models.crm import (
    CRMStage as CRMStage,
)
from venture_intelligence.shared.models.crm import (
    Investigation as Investigation,
)
from venture_intelligence.shared.models.crm import (
    InvestigationStatus as InvestigationStatus,
)
from venture_intelligence.shared.models.entities import (
    Company as Company,
)
from venture_intelligence.shared.models.entities import (
    Person as Person,
)
from venture_intelligence.shared.models.evaluation import (
    EvaluationResult as EvaluationResult,
)
from venture_intelligence.shared.models.events import (
    AcceleratorProgram as AcceleratorProgram,
)
from venture_intelligence.shared.models.events import (
    CanonicalEvent as CanonicalEvent,
)
from venture_intelligence.shared.models.events import (
    EntityType as EntityType,
)
from venture_intelligence.shared.models.events import (
    EventType as EventType,
)
from venture_intelligence.shared.models.events import (
    Source as Source,
)
from venture_intelligence.shared.models.events import (
    SpeedrunEventType as SpeedrunEventType,
)
from venture_intelligence.shared.models.events import (
    TemporalPrecision as TemporalPrecision,
)
from venture_intelligence.shared.models.events import (
    TemporalStatus as TemporalStatus,
)
from venture_intelligence.shared.models.events import (
    TemporalValidity as TemporalValidity,
)
from venture_intelligence.shared.models.evidence import (
    Evidence as Evidence,
)
from venture_intelligence.shared.models.evidence import (
    Feature as Feature,
)
from venture_intelligence.shared.models.evidence import (
    Hypothesis as Hypothesis,
)
from venture_intelligence.shared.models.evidence import (
    Observation as Observation,
)
from venture_intelligence.shared.models.evidence import (
    PersonClassification as PersonClassification,
)
from venture_intelligence.shared.models.evidence import (
    PersonType as PersonType,
)
from venture_intelligence.shared.models.evidence import (
    Signal as Signal,
)
from venture_intelligence.shared.models.evidence import (
    SignalType as SignalType,
)
from venture_intelligence.shared.models.graph import (
    GraphEdge as GraphEdge,
)
from venture_intelligence.shared.models.graph import (
    GraphEdgeType as GraphEdgeType,
)
from venture_intelligence.shared.models.graph import (
    GraphNode as GraphNode,
)
from venture_intelligence.shared.models.graph import (
    GraphNodeType as GraphNodeType,
)
from venture_intelligence.shared.models.ranking import (
    DIMENSION_WEIGHTS as DIMENSION_WEIGHTS,
)
from venture_intelligence.shared.models.ranking import (
    FounderRanking as FounderRanking,
)
from venture_intelligence.shared.models.ranking import (
    RankingDimension as RankingDimension,
)
from venture_intelligence.shared.models.research import (
    EvidenceAttributionStatus as EvidenceAttributionStatus,
)
from venture_intelligence.shared.models.research import (
    HistoricalWebObservation as HistoricalWebObservation,
)
from venture_intelligence.shared.models.research import (
    ResearchBudget as ResearchBudget,
)
from venture_intelligence.shared.models.research import (
    ResearchDiscoveryMethod as ResearchDiscoveryMethod,
)
from venture_intelligence.shared.models.research import (
    ResearchEntity as ResearchEntity,
)
from venture_intelligence.shared.models.research import (
    ResearchEvidence as ResearchEvidence,
)
from venture_intelligence.shared.models.research import (
    ResearchHypothesis as ResearchHypothesis,
)
from venture_intelligence.shared.models.research import (
    ResearchIdentityStatus as ResearchIdentityStatus,
)
from venture_intelligence.shared.models.research import (
    ResearchProgramStatus as ResearchProgramStatus,
)
from venture_intelligence.shared.models.research import (
    ResearchProviderSkip as ResearchProviderSkip,
)
from venture_intelligence.shared.models.research import (
    ResearchQuery as ResearchQuery,
)
from venture_intelligence.shared.models.research import (
    ResearchQueryIntent as ResearchQueryIntent,
)
from venture_intelligence.shared.models.research import (
    ResearchRejectedResult as ResearchRejectedResult,
)
from venture_intelligence.shared.models.research import (
    ResearchRun as ResearchRun,
)
from venture_intelligence.shared.models.research import (
    ResearchSource as ResearchSource,
)
from venture_intelligence.shared.models.research import (
    ResearchSourceStatus as ResearchSourceStatus,
)
from venture_intelligence.shared.models.research import (
    ResearchTarget as ResearchTarget,
)
from venture_intelligence.shared.models.research import (
    ResearchTargetType as ResearchTargetType,
)
from venture_intelligence.shared.models.research import (
    SourceAccessMode as SourceAccessMode,
)
from venture_intelligence.shared.models.research import (
    SourceOperationStatus as SourceOperationStatus,
)
from venture_intelligence.shared.models.research import (
    WebsiteLifecycleSignal as WebsiteLifecycleSignal,
)
