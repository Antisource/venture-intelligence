"""Compute versioned, program-generic signals from canonical features.

Each signal is a normalized 0.0–1.0 float representing how strongly an entity
exhibits that quality.  The ranking worker then maps signals onto the 9 ranking
dimensions to produce a composite score.

Signal types include:
  program_affinity   — strength of validated accelerator/program membership
  technical_depth    — GitHub activity, repo count, languages, commit velocity
  hiring_velocity    — open roles per 30-day window
  research_founder   — published academic or technical papers
  stealth_startup    — public activity without confirmed program membership
  open_source_leader — GitHub stars, forks, follower count
  repeat_founder     — prior companies visible in the knowledge graph

After computing signals, the worker also generates Hypotheses — high-level
probabilistic statements (e.g. "Likely building deep tech") that partners
see in the UI alongside raw scores.

Design: all signal computations are pure functions of DB state.
No network calls here — connectors have already fetched the data.
"""

import hashlib as _hashlib
import json
import math
import uuid as _uuid
from datetime import datetime, timezone

from venture_intelligence.pipeline.feature_worker import FeatureWorker
from venture_intelligence.shared.models import Feature, Hypothesis, Signal, SignalType
from venture_intelligence.storage.db import Database

# M4+ authoritative implementation. Persisted YC aliases remain readable at
# explicit compatibility boundaries, but new signals use generic semantics.
SIGNAL_VERSION = "m5-signal-1"
HYPOTHESIS_VERSION = "m4-hypothesis-1"
_SIGNAL_NAMESPACE = _uuid.UUID("e67dd7ed-9ba7-5718-b65d-b0e0b2f7a307")
_HYPOTHESIS_NAMESPACE = _uuid.UUID("31429869-d569-5eed-9ecf-df040f10cd49")


class FeatureSignalWorker:
    """Compute normalized signals exclusively from versioned features."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._features = FeatureWorker(db)

    def process_entity(
        self,
        entity_id: str,
        *,
        as_of: datetime | None = None,
        input_as_of: datetime | None = None,
    ) -> list[Signal]:
        cutoff = _m4_utc(as_of or datetime.now(timezone.utc))
        features = self._features.process_entity(
            entity_id,
            as_of=cutoff,
            input_as_of=input_as_of,
        )
        if not features:
            return []
        by_name = {feature.name: feature for feature in features}
        signals = [
            self._one(
                entity_id,
                SignalType.PROGRAM_AFFINITY,
                _m4_value(by_name, "program_membership_confidence"),
                _m4_select(by_name, "program_membership_confidence"),
                "Validated accelerator or program membership evidence",
                cutoff,
            ),
            self._technical_depth(entity_id, by_name, cutoff),
            self._scaled(
                entity_id,
                SignalType.HIRING_VELOCITY,
                by_name,
                "open_roles_30d",
                5.0,
                "Open roles observed in the trailing 30-day feature window",
                cutoff,
            ),
            self._scaled(
                entity_id,
                SignalType.RESEARCH_FOUNDER,
                by_name,
                "research_publication_count",
                2.0,
                "Public research outputs linked to this entity",
                cutoff,
            ),
            self._stealth(entity_id, by_name, cutoff),
            self._open_source(entity_id, by_name, cutoff),
            self._scaled(
                entity_id,
                SignalType.HUMAN_FEEDBACK_POSITIVE,
                by_name,
                "human_feedback_positive_count",
                3.0,
                "Auditable positive human workflow actions; not ground truth",
                cutoff,
            ),
            self._scaled(
                entity_id,
                SignalType.HUMAN_FEEDBACK_NEGATIVE,
                by_name,
                "human_feedback_negative_count",
                1.0,
                "Auditable passed decisions; a preference signal, not ground truth",
                cutoff,
            ),
            self._scaled(
                entity_id,
                SignalType.REALIZED_OUTCOME,
                by_name,
                "realized_outcome_count",
                1.0,
                "Recorded external investment outcome; separately typed from human judgment",
                cutoff,
            ),
        ]
        signals = [signal for signal in signals if signal.value > 0]
        for signal in signals:
            self._db.write_signal(signal)
        hypotheses = self._generate_hypotheses(entity_id, signals, cutoff)
        for hypothesis in hypotheses:
            self._db.write_hypothesis(hypothesis)
        return signals

    def process_entity_speedrun(self, entity_id: str) -> list[Signal]:
        """Compatibility entry point; program semantics come from metadata."""
        return self.process_entity(entity_id)

    def _technical_depth(
        self,
        entity_id: str,
        features: dict[str, Feature],
        cutoff: datetime,
    ) -> Signal:
        selected = _m4_select(features, "repository_count", "github_commits_7d", "github_stars")
        repository = min(_m4_value(features, "repository_count"), 1.0) * 0.30
        commits = min(_m4_value(features, "github_commits_7d") / 50.0, 1.0) * 0.50
        stars = (
            min(
                math.log(_m4_value(features, "github_stars") + 1) / math.log(1001),
                1.0,
            )
            * 0.20
        )
        return self._one(
            entity_id,
            SignalType.TECHNICAL_DEPTH,
            min(repository + commits + stars, 1.0),
            selected,
            "Repository presence, commit velocity, and stars",
            cutoff,
        )

    def _open_source(
        self,
        entity_id: str,
        features: dict[str, Feature],
        cutoff: datetime,
    ) -> Signal:
        selected = _m4_select(features, "github_stars")
        stars = _m4_value(features, "github_stars")
        value = min(math.log(stars + 1) / math.log(10_001), 1.0) if stars else 0.0
        return self._one(
            entity_id,
            SignalType.OPEN_SOURCE_LEADER,
            value,
            selected,
            f"{int(stars)} entity-linked public repository stars",
            cutoff,
        )

    def _stealth(
        self,
        entity_id: str,
        features: dict[str, Feature],
        cutoff: datetime,
    ) -> Signal:
        activity = _m4_select(
            features,
            "repository_count",
            "company_website_present",
            "public_launch_count",
        )
        confirmed = _m4_value(features, "program_membership_confidence") > 0
        return self._one(
            entity_id,
            SignalType.STEALTH_STARTUP,
            0.8 if activity and not confirmed else 0.0,
            activity,
            "Public building activity exists without validated program membership",
            cutoff,
        )

    def _scaled(
        self,
        entity_id: str,
        signal_type: SignalType,
        features: dict[str, Feature],
        feature_name: str,
        divisor: float,
        explanation: str,
        cutoff: datetime,
    ) -> Signal:
        selected = _m4_select(features, feature_name)
        return self._one(
            entity_id,
            signal_type,
            min(_m4_value(features, feature_name) / divisor, 1.0),
            selected,
            explanation,
            cutoff,
        )

    def _one(
        self,
        entity_id: str,
        signal_type: SignalType,
        value: float,
        features: list[Feature],
        explanation: str,
        cutoff: datetime,
    ) -> Signal:
        feature_ids = sorted(feature.feature_id for feature in features)
        evidence_ids = sorted({item for feature in features for item in feature.evidence_ids})
        crm_action_ids = sorted({item for feature in features for item in feature.crm_action_ids})
        confidence = (
            sum(feature.confidence for feature in features) / len(features) if features else 0.0
        )
        semantics = {
            "entity_id": entity_id,
            "signal_type": signal_type.value,
            "value": round(value, 6),
            "feature_ids": feature_ids,
            "crm_action_ids": crm_action_ids,
            "as_of": cutoff.isoformat(),
            "signal_version": SIGNAL_VERSION,
        }
        digest = _m4_digest(semantics)
        return Signal(
            signal_id=str(_uuid.uuid5(_SIGNAL_NAMESPACE, digest)),
            signal_version=SIGNAL_VERSION,
            entity_id=entity_id,
            signal_type=signal_type,
            value=round(value, 6),
            evidence_ids=evidence_ids,
            feature_ids=feature_ids,
            crm_action_ids=crm_action_ids,
            input_digest=digest,
            explanation=explanation,
            confidence=round(confidence, 6),
            observed_at=cutoff,
        )

    def _generate_hypotheses(
        self,
        entity_id: str,
        signals: list[Signal],
        cutoff: datetime,
    ) -> list[Hypothesis]:
        values = {signal.signal_type: signal for signal in signals}
        program = _m4_program_signal_value(values)
        technical = _m4_signal_value(values, SignalType.TECHNICAL_DEPTH)
        hiring = _m4_signal_value(values, SignalType.HIRING_VELOCITY)
        research = _m4_signal_value(values, SignalType.RESEARCH_FOUNDER)
        open_source = _m4_signal_value(values, SignalType.OPEN_SOURCE_LEADER)
        definitions: list[tuple[str, float, tuple[SignalType, ...]]] = []
        if program >= 0.95:
            definitions.append(
                (
                    "Confirmed accelerator or program association",
                    program,
                    (SignalType.PROGRAM_AFFINITY,),
                )
            )
        if technical >= 0.5 and program >= 0.5:
            definitions.append(
                (
                    "Technical team actively building with corroborated program evidence",
                    (technical + program) / 2,
                    (SignalType.TECHNICAL_DEPTH, SignalType.PROGRAM_AFFINITY),
                )
            )
        if hiring >= 0.5:
            definitions.append(
                (
                    "Company is scaling through observable hiring activity",
                    hiring,
                    (SignalType.HIRING_VELOCITY,),
                )
            )
        if research >= 0.5:
            definitions.append(
                (
                    "Founder or team has a public research background",
                    research * 0.9,
                    (SignalType.RESEARCH_FOUNDER,),
                )
            )
        if open_source >= 0.4:
            definitions.append(
                (
                    "Open-source activity may provide an existing community advantage",
                    open_source * 0.85,
                    (SignalType.OPEN_SOURCE_LEADER,),
                )
            )
        return [
            _m4_hypothesis(entity_id, statement, probability, kinds, values, cutoff)
            for statement, probability, kinds in definitions
        ]


def _m4_hypothesis(
    entity_id: str,
    statement: str,
    probability: float,
    kinds: tuple[SignalType, ...],
    signals: dict[SignalType, Signal],
    cutoff: datetime,
) -> Hypothesis:
    signal_ids = sorted(signals[kind].signal_id for kind in kinds if kind in signals)
    semantics = {
        "entity_id": entity_id,
        "statement": statement,
        "probability": round(probability, 6),
        "signal_ids": signal_ids,
        "as_of": cutoff.isoformat(),
        "hypothesis_version": HYPOTHESIS_VERSION,
    }
    digest = _m4_digest(semantics)
    return Hypothesis(
        hypothesis_id=str(_uuid.uuid5(_HYPOTHESIS_NAMESPACE, digest)),
        hypothesis_version=HYPOTHESIS_VERSION,
        entity_id=entity_id,
        statement=statement,
        signal_ids=signal_ids,
        input_digest=digest,
        probability=round(probability, 6),
        created_at=cutoff,
    )


def _m4_select(features: dict[str, Feature], *names: str) -> list[Feature]:
    return [features[name] for name in names if name in features]


def _m4_value(features: dict[str, Feature], name: str) -> float:
    return features[name].value if name in features else 0.0


def _m4_signal_value(signals: dict[SignalType, Signal], kind: SignalType) -> float:
    return signals[kind].value if kind in signals else 0.0


def _m4_program_signal_value(signals: dict[SignalType, Signal]) -> float:
    """Prefer M6 semantics while accepting persisted pre-M6 signal snapshots."""
    return _m4_signal_value(signals, SignalType.PROGRAM_AFFINITY) or _m4_signal_value(
        signals, SignalType.YC_LIKELIHOOD
    )


def _m4_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("signal as_of must include a UTC offset")
    return value.astimezone(timezone.utc)


def _m4_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return _hashlib.sha256(encoded.encode()).hexdigest()


SignalWorker = FeatureSignalWorker
