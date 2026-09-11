"""
pipeline/classification_worker.py — determines person type (§16).

The Classification Worker labels each Person entity as one of:
  FOUNDER    — has FOUNDED edges or founder title keywords
  INVESTOR   — has INVESTED_BY edges as source, or "partner/investor" in title
  RESEARCHER — has AUTHORED edges (papers) or academic institution
  OPERATOR   — none of the above; likely a functional hire
  UNKNOWN    — insufficient signals to classify

Why this matters for ranking:
  The 9 ranking dimensions have different weights for different person types.
  A researcher's "research_output" dimension matters more than an operator's.
  Classification allows the Ranking Worker to apply type-appropriate weights.

Classification rules (applied in order, first match wins):
  1. Has FOUNDED edge in knowledge graph → FOUNDER
  2. Title contains founder/co-founder/ceo keywords → FOUNDER
  3. Has AUTHORED edges (papers) → RESEARCHER
  4. Title contains investor/partner/principal keywords → INVESTOR
  5. Otherwise → OPERATOR
"""

import logging

from venture_intelligence.shared.models import PersonClassification, PersonType
from venture_intelligence.storage.db import Database

logger = logging.getLogger(__name__)

# Keyword sets for title-based classification
_FOUNDER_KEYWORDS = {"founder", "co-founder", "ceo", "cofounder", "building", "started"}
_INVESTOR_KEYWORDS = {"investor", "partner", "principal", "vc", "venture", "angel"}
_RESEARCHER_KEYWORDS = {"researcher", "phd", "professor", "scientist", "fellow", "postdoc"}


class ClassificationWorker:
    """Labels Person entities with their most likely role."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def classify_entity(self, entity_id: str) -> PersonClassification | None:
        """
        Classify one entity and persist the result.

        Returns None if the entity is a Company (only Persons are classified).
        """
        person = self._db.get_person(entity_id)
        if not person:
            return None  # companies are not classified

        evidence = self._db.get_evidence_for_entity(entity_id)
        evidence_ids = [e.evidence_id for e in evidence]

        person_type, confidence = self._classify(person, entity_id)

        clf = PersonClassification(
            entity_id=entity_id,
            person_type=person_type,
            confidence=confidence,
            evidence_ids=evidence_ids,
        )
        self._db.write_classification(clf)
        logger.debug(
            "classified entity=%s as %s (conf=%.2f)",
            entity_id,
            person_type.value,
            confidence,
        )
        return clf

    def classify_all(self) -> int:
        """Classify every Person entity in the database. Returns count classified."""
        persons = self._db.all_persons()
        count = 0
        for person in persons:
            try:
                result = self.classify_entity(person.person_id)
                if result:
                    count += 1
            except Exception as exc:
                logger.warning("classification failed for %s: %s", person.person_id, exc)
        return count

    # ── Private ───────────────────────────────────────────────────────────────

    def _classify(self, person, entity_id: str) -> tuple[PersonType, float]:
        """
        Apply classification rules in priority order.
        Returns (PersonType, confidence).
        """
        # Rule 1: Knowledge graph — FOUNDED edge is definitive
        neighbors = self._db.get_node_neighbors(entity_id, edge_type="FOUNDED")
        if neighbors:
            return PersonType.FOUNDER, 0.95

        title = (person.current_title or "").lower()

        # Rule 2: Title keywords — founder
        if any(kw in title for kw in _FOUNDER_KEYWORDS):
            return PersonType.FOUNDER, 0.88

        # Rule 3: Knowledge graph — AUTHORED edge → researcher
        authored = self._db.get_node_neighbors(entity_id, edge_type="AUTHORED")
        if authored:
            return PersonType.RESEARCHER, 0.90

        # Rule 4: Title keywords — researcher
        if any(kw in title for kw in _RESEARCHER_KEYWORDS):
            return PersonType.RESEARCHER, 0.82

        # Rule 5: Title keywords — investor
        if any(kw in title for kw in _INVESTOR_KEYWORDS):
            return PersonType.INVESTOR, 0.80

        # Fallback
        if title:
            return PersonType.OPERATOR, 0.65
        return PersonType.UNKNOWN, 0.40
