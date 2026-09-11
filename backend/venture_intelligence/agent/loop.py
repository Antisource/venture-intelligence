"""
agent/loop.py — THE agentic core of the system.

This is the soul of the platform.

The difference between a periodic scraper and an agentic system:
  Scraper   — sweeps sources on a clock tick, indiscriminately.
  Agent     — reasons about the current state, identifies gaps,
              and dispatches targeted investigations to fill them.

The AgentLoop runs every AGENT_INTERVAL seconds and:

  1. PRIORITISE  — ranks all tracked entities by "investigation value":
                   high ranking + low confidence = most worth investigating.

  2. TARGET      — for the top-N priority entities, synthesises investigation
                   trigger events and routes them through the ConnectorCoordinator.
                   This is not a random sweep — it is purposeful intelligence work.

  3. REFRESH     — identifies entities not seen in > STALE_HOURS and refreshes them.

  4. LEARN       — reads CRM partner decisions (PASSED, INTERESTED, INVESTED)
                   and adjusts entity confidence weights for future rankings.

  5. SURFACE     — if a previously PASSED entity's signals materially improve,
                   the agent re-surfaces it to the CRM with a "signals changed" note.

The agent never makes investment decisions. It makes investigation decisions.
Partners remain the decision-makers (§6.7 Human Feedback).

Agentic properties:
  Autonomy     — runs without human intervention.
  Reactivity   — responds to CRM signals and ranking gaps.
  Proactivity  — initiates investigation before signals are obvious.
  Goal-directed — optimises for evidence density on high-potential entities.
"""

import asyncio
import logging
from datetime import datetime, timezone

from venture_intelligence.connectors.base import ConnectorCoordinator
from venture_intelligence.pipeline.entity_integrity import validated_entity_record
from venture_intelligence.shared.bus import EventBus
from venture_intelligence.shared.models import (
    CanonicalEvent,
    CRMStage,
    EntityType,
    EventType,
    Source,
)
from venture_intelligence.storage.db import Database

logger = logging.getLogger(__name__)

# How often the agent runs its reasoning loop (seconds)
AGENT_INTERVAL = 300  # every 5 minutes

# How many top entities to actively investigate per agent tick
INVESTIGATION_BUDGET = 10

# Hours after which an entity is considered stale
STALE_HOURS = 12

# Minimum composite score for an entity to be worth re-investigating
MIN_SCORE_FOR_REINVESTIGATION = 0.30

# If a PASSED entity's composite score improves by this much, re-surface it
RESURFACING_SCORE_DELTA = 0.15


class AgentLoop:
    """
    Autonomous investigation planner.

    The agent reasons about WHERE to focus investigation resources,
    not just WHEN to run sweeps.  It is the strategic layer that sits
    above the tactical connector scan loops.
    """

    def __init__(
        self,
        db: Database,
        bus: EventBus,
        coordinator: ConnectorCoordinator,
    ) -> None:
        self._db = db
        self._bus = bus
        self._coordinator = coordinator
        # entity_id → composite_score at time of PASSED decision
        # Used to detect when a passed entity's signals improve materially
        self._passed_scores: dict[str, float] = {}

    async def run(self) -> None:
        """
        Main agent loop — runs until cancelled.
        Each tick is an independent reasoning cycle.
        """
        logger.info(
            "AgentLoop started (interval=%ds, budget=%d)",
            AGENT_INTERVAL,
            INVESTIGATION_BUDGET,
        )
        while True:
            try:
                await self._tick()
            except Exception as exc:
                # Never let a bad tick kill the loop
                logger.error("AgentLoop tick error: %s", exc)
            await asyncio.sleep(AGENT_INTERVAL)

    async def _tick(self) -> None:
        """
        One reasoning cycle.  Steps run in sequence so each step
        can use the outputs of the previous one.
        """
        now = datetime.now(timezone.utc)
        logger.info("AgentLoop tick starting at %s", now.isoformat())

        # Step 1 — Prioritise: which entities need more investigation?
        targets = self._prioritise()
        logger.info("AgentLoop: %d entities selected for investigation", len(targets))

        # Step 2 — Target: route investigation triggers for each
        dispatched = 0
        for entity_id, reason in targets:
            trigger = self._build_trigger(entity_id, reason)
            self._bus.publish(trigger)
            count = await self._coordinator.route(trigger)
            dispatched += count
        logger.info("AgentLoop: dispatched %d follow-up events", dispatched)

        # Step 3 — Refresh stale entities
        stale = await self._refresh_stale()
        logger.info("AgentLoop: refreshed %d stale entities", stale)

        # Step 4 — Learn from CRM decisions
        self._learn_from_crm()

        # Step 5 — Re-surface any PASSED entities whose signals improved
        resurfaced = self._resurface_improved()
        if resurfaced:
            logger.info("AgentLoop: re-surfaced %d previously-passed entities", resurfaced)

    # ═══════════════════════════════════════════════════════════════════════════
    # Step 1: Prioritisation
    # ═══════════════════════════════════════════════════════════════════════════

    def _prioritise(self) -> list[tuple[str, str]]:
        """
        Score every tracked entity by "investigation value" and return the
        top INVESTIGATION_BUDGET candidates.

        Investigation value = composite_score × (1 - confidence)

        Intuition: a high-ranked entity we're uncertain about is worth
        more investigation than a low-ranked entity we're very sure about.

        Special priority boost:
          • CRM stage > DISCOVERY (partner has expressed interest)
          • Stealth signal present (pre-announcement window is time-limited)
        """
        rankings = self._db.get_rankings(limit=500)
        crm_board = self._db.get_crm_board()

        # Build sets of entity IDs in active CRM stages
        active_crm_ids = {
            row["entity_id"]
            for stage, rows in crm_board.items()
            for row in rows
            if stage not in (CRMStage.PASSED.value, CRMStage.DISCOVERY.value)
        }

        candidates: list[tuple[float, str, str]] = []

        for r in rankings:
            if not self._is_promoted_entity(r.entity_id):
                continue
            if r.composite_score < MIN_SCORE_FOR_REINVESTIGATION:
                continue

            # Base investigation value
            inv_value = r.composite_score * (1.0 - r.confidence)

            # Boost for entities partners are actively watching
            if r.entity_id in active_crm_ids:
                inv_value *= 1.5
                reason = f"Partner-active (score={r.composite_score:.2f}, conf={r.confidence:.2f})"
            else:
                reason = (
                    "High-rank low-confidence "
                    f"(score={r.composite_score:.2f}, conf={r.confidence:.2f})"
                )

            candidates.append((inv_value, r.entity_id, reason))

        # Sort by investigation value descending, take top N
        candidates.sort(reverse=True, key=lambda x: x[0])
        return [(entity_id, reason) for _, entity_id, reason in candidates[:INVESTIGATION_BUDGET]]

    # ═══════════════════════════════════════════════════════════════════════════
    # Step 2: Build investigation triggers
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_trigger(self, entity_id: str, reason: str) -> CanonicalEvent:
        """
        Synthesise a CanonicalEvent that re-triggers cross-connector investigation.

        The event's entity_id and raw_payload carry enough context for each
        connector's investigate() method to do targeted lookups.
        The metadata marks it as agent-generated so the pipeline can route it
        differently if needed (e.g. higher priority queue in V2).
        """
        company = self._db.get_company(entity_id)
        person = self._db.get_person(entity_id)
        entity = company or person
        name = entity.canonical_name if entity else entity_id

        # Determine which event type best triggers the right investigations
        event_type = EventType.COMPANY_LISTED if company else EventType.FOUNDER_DETECTED
        entity_type = EntityType.COMPANY if company else EntityType.PERSON

        return CanonicalEvent(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            source=Source.CRM,  # agent-generated; not from external source
            source_url=f"agent://investigation/{entity_id}",
            occurred_at=datetime.now(timezone.utc),
            confidence=0.90,
            raw_payload={
                "name": name,
                "company_name": (
                    name if company else (entity.current_company_id if person else None)
                ),
                "github_username": person.github_username if person else None,
                "reason": reason,
            },
            metadata={
                "investigation_depth": 0,
                "agent_generated": True,
                "trigger_reason": reason,
            },
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # Step 3: Refresh stale entities
    # ═══════════════════════════════════════════════════════════════════════════

    async def _refresh_stale(self) -> int:
        """
        Re-trigger investigation for entities not seen in STALE_HOURS.

        The agent only refreshes entities that are ranked (have signals) —
        there's no point refreshing entities that scored 0.
        """
        stale_ids = self._db.get_stale_entity_ids(older_than_hours=STALE_HOURS)
        ranked_ids = {r.entity_id for r in self._db.get_rankings(limit=500)}

        refreshed = 0
        for entity_id in stale_ids:
            if entity_id not in ranked_ids or not self._is_promoted_entity(entity_id):
                continue
            trigger = self._build_trigger(entity_id, f"Stale — not seen in >{STALE_HOURS}h")
            self._bus.publish(trigger)
            await self._coordinator.route(trigger)
            refreshed += 1
            if refreshed >= INVESTIGATION_BUDGET:
                break  # respect the budget even for refreshes

        return refreshed

    def _is_promoted_entity(self, entity_id: str) -> bool:
        """Require the same canonical validation boundary as ranking/API views."""
        entity = self._db.get_company(entity_id) or self._db.get_person(entity_id)
        return bool(
            entity and self._db.is_entity_validated(entity_id) and validated_entity_record(entity)
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # Step 4: Learn from CRM feedback
    # ═══════════════════════════════════════════════════════════════════════════

    def _learn_from_crm(self) -> None:
        """
        Read partner CRM decisions and use them to calibrate the system.

        PASSED entities: record their current score in _passed_scores
                         (used in Step 5 to detect improvement).

        Typed M5 CRM actions are converted into bounded feedback features by
        FeatureWorker.  This loop never promotes a partner decision to ground
        truth or writes an unlineaged feature.
        """
        board = self._db.get_crm_board()

        for row in board.get(CRMStage.PASSED.value, []):
            entity_id = row["entity_id"]
            if not self._is_promoted_entity(entity_id):
                continue
            ranking = self._db.get_ranking_for_entity(entity_id)
            if ranking and entity_id not in self._passed_scores:
                # Record score at time of PASS so we can detect if it improves
                self._passed_scores[entity_id] = ranking.composite_score

    # ═══════════════════════════════════════════════════════════════════════════
    # Step 5: Re-surface improved PASSED entities
    # ═══════════════════════════════════════════════════════════════════════════

    def _resurface_improved(self) -> int:
        """
        If a PASSED entity's composite score has risen by RESURFACING_SCORE_DELTA
        since it was passed, move it back to DISCOVERY with a note.

        Example: a founder was PASSED at score 0.35 in April.  In August,
        new GitHub signals push their score to 0.55.  The agent re-surfaces
        them with the note "Signals improved +0.20 since last review."
        """
        if not self._passed_scores:
            return 0

        resurfaced = 0
        for entity_id, passed_score in list(self._passed_scores.items()):
            if not self._is_promoted_entity(entity_id):
                del self._passed_scores[entity_id]
                continue
            current = self._db.get_ranking_for_entity(entity_id)
            if not current:
                continue
            delta = current.composite_score - passed_score
            if delta >= RESURFACING_SCORE_DELTA:
                try:
                    from venture_intelligence.crm.workflow import CRMWorkflow

                    crm = CRMWorkflow(self._db)
                    crm.advance_stage(
                        entity_id,
                        CRMStage.DISCOVERY,
                        partner="agent",
                        note=(
                            f"Auto-resurfaced: composite score improved "
                            f"+{delta:.2f} since last review "
                            f"({passed_score:.2f} → {current.composite_score:.2f})"
                        ),
                    )
                    del self._passed_scores[entity_id]
                    resurfaced += 1
                    logger.info(
                        "AgentLoop: re-surfaced entity=%s delta=+%.2f",
                        entity_id,
                        delta,
                    )
                except Exception as exc:
                    logger.warning("Re-surface failed for %s: %s", entity_id, exc)

        return resurfaced
