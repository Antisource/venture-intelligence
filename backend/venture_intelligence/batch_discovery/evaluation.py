"""Isolated deterministic M8 discovery evaluation helpers."""

from __future__ import annotations

from datetime import datetime

from venture_intelligence.batch_discovery.engine import build_hypotheses
from venture_intelligence.batch_discovery.models import (
    BatchDiscoveryEvaluation,
    BatchDiscoveryObservation,
    CandidateHypothesis,
)
from venture_intelligence.programs import ProgramConfig


def evaluate_discovery(
    candidates: list[CandidateHypothesis],
    *,
    ground_truth: dict[str, datetime],
    k: int = 10,
    observations: list[BatchDiscoveryObservation] | None = None,
    program: ProgramConfig | None = None,
) -> BatchDiscoveryEvaluation:
    """Evaluate promoted discovery output without consulting CRM or live rankings."""
    promoted = [item for item in candidates if item.assessment.promotion_eligible]
    promoted.sort(key=lambda item: (-item.assessment.confidence_pct, item.display_name.casefold()))
    truth = {name.casefold(): timestamp for name, timestamp in ground_truth.items()}
    top = promoted[: max(1, k)]
    top_names = {(item.company_name or item.display_name).casefold() for item in top}
    all_names = {(item.company_name or item.display_name).casefold() for item in promoted}
    true_top = top_names & set(truth)
    true_all = all_names & set(truth)
    false_positive = sorted(top_names - set(truth))
    missed = sorted(set(truth) - all_names)
    lead_times = [
        (
            truth[(item.company_name or item.display_name).casefold()] - item.first_seen_at
        ).total_seconds()
        / 86_400
        for item in promoted
        if (item.company_name or item.display_name).casefold() in truth
    ]
    contribution: dict[str, int] = {}
    for item in promoted:
        for source in item.assessment.source_diversity:
            contribution[source] = contribution.get(source, 0) + 1
    ablation: dict[str, int] = {}
    if observations is not None and program is not None:
        baseline = {(item.company_name or item.display_name).casefold() for item in promoted}
        for domain in sorted({item.source_domain for item in observations}):
            rebuilt = build_hypotheses(
                [item for item in observations if item.source_domain != domain],
                run_id=f"evaluation-ablation-{domain}",
                program=program,
                revision_for=lambda _hypothesis_id: 1,
            )
            eligible = {
                (item.company_name or item.display_name).casefold()
                for item in rebuilt
                if item.assessment.promotion_eligible
            }
            ablation[domain] = len(baseline - eligible)
    brier_terms = [
        (
            item.assessment.confidence_pct / 100
            - int((item.company_name or item.display_name).casefold() in truth)
        )
        ** 2
        for item in candidates
    ]
    return BatchDiscoveryEvaluation(
        precision_at_k=len(true_top) / len(top) if top else 0.0,
        recall=len(true_all) / len(truth) if truth else 0.0,
        false_positive_rate=len(false_positive) / len(top) if top else 0.0,
        mean_lead_time_days=sum(lead_times) / len(lead_times) if lead_times else None,
        missed_entities=missed,
        false_positive_entities=false_positive,
        identity_resolution_errors=[
            item.display_name for item in promoted if item.assessment.identity_confidence_pct < 80
        ],
        source_contribution=dict(sorted(contribution.items())),
        source_ablation_promotion_loss=ablation,
        acceptance_confidence_brier_score=(
            sum(brier_terms) / len(brier_terms) if brier_terms else None
        ),
    )
