"""Star-import surface of ``venture_intelligence.shared.models``.

The expected names are the public names the former single-file
``shared/models.py`` exported via ``import *`` before it became a package.
"""

EXPECTED_STAR_NAMES = {
    "AcceleratorProgram",
    "Alert",
    "AlertLifecycleEvent",
    "AlertLifecycleStatus",
    "Any",
    "BaseModel",
    "CRMActionType",
    "CRMEvent",
    "CRMFeedbackKind",
    "CRMRecord",
    "CRMStage",
    "CRM_TRANSITIONS",
    "CanonicalEvent",
    "Company",
    "DIMENSION_WEIGHTS",
    "EntityType",
    "Enum",
    "EvaluationResult",
    "EventType",
    "Evidence",
    "EvidenceAttributionStatus",
    "Feature",
    "Field",
    "FounderRanking",
    "GraphEdge",
    "GraphEdgeType",
    "GraphNode",
    "GraphNodeType",
    "HistoricalWebObservation",
    "Hypothesis",
    "Investigation",
    "InvestigationStatus",
    "Observation",
    "Person",
    "PersonClassification",
    "PersonType",
    "RankingDimension",
    "Recommendation",
    "RecommendationAction",
    "RecommendationLifecycleEvent",
    "RecommendationLifecycleStatus",
    "ResearchBudget",
    "ResearchDiscoveryMethod",
    "ResearchEntity",
    "ResearchEvidence",
    "ResearchHypothesis",
    "ResearchIdentityStatus",
    "ResearchProgramStatus",
    "ResearchProviderSkip",
    "ResearchQuery",
    "ResearchQueryIntent",
    "ResearchRejectedResult",
    "ResearchRun",
    "ResearchSource",
    "ResearchSourceStatus",
    "ResearchTarget",
    "ResearchTargetType",
    "Signal",
    "SignalType",
    "Source",
    "SourceAccessMode",
    "SourceOperationStatus",
    "SpeedrunEventType",
    "TemporalPrecision",
    "TemporalStatus",
    "TemporalValidity",
    "WebsiteLifecycleSignal",
    "datetime",
    "field_validator",
    "model_validator",
    "timezone",
    "uuid4",
}

SUBMODULE_NAMES = {
    "_helpers",
    "alerts",
    "crm",
    "entities",
    "evaluation",
    "events",
    "evidence",
    "graph",
    "ranking",
    "research",
}


def _star_import() -> dict:
    namespace: dict = {}
    exec("from venture_intelligence.shared.models import *", namespace)
    namespace.pop("__builtins__", None)
    return namespace


def test_star_import_exports_exactly_former_module_names():
    assert set(_star_import()) == EXPECTED_STAR_NAMES


def test_star_import_does_not_export_submodules():
    assert SUBMODULE_NAMES.isdisjoint(_star_import())


def test_star_import_does_not_clobber_same_named_globals():
    namespace: dict = {"events": ["sentinel"], "graph": object()}
    graph = namespace["graph"]
    exec("from venture_intelligence.shared.models import *", namespace)
    assert namespace["events"] == ["sentinel"]
    assert namespace["graph"] is graph
