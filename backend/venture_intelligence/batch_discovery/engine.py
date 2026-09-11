"""Pure extraction, clustering, assessment, and profile projection for M8."""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from venture_intelligence.batch_discovery.models import (
    BatchDiscoveryObservation,
    BatchParticipationAssessment,
    CandidateHypothesis,
    CandidateKind,
    CandidateProfile,
    CandidateState,
    ConfidenceComponent,
    DiscoveryDropReason,
    FundingClaimKind,
    FundingRoundEvidence,
    FundingStatus,
    InvestorRelationship,
    InvestorSignal,
    ObservationStance,
    SourceCoverage,
    SourceCoverageItem,
    SourceIndependence,
    SourceTaxonomyRole,
)
from venture_intelligence.pipeline.entity_integrity import is_generic_label
from venture_intelligence.programs import ProgramConfig
from venture_intelligence.research.query import is_probable_person_name
from venture_intelligence.research.sources import SearchResult, extract_venture_metadata

_NAMESPACE = uuid.UUID("5e27639f-c888-5dd5-a163-8ac49c7b0a95")
_RESTRICTED_SOCIAL = {
    "linkedin.com",
    "x.com",
    "twitter.com",
    "instagram.com",
    "threads.net",
}
_TECHNICAL = {"github.com", "huggingface.co", "pypi.org", "npmjs.com", "arxiv.org", "orcid.org"}
_PARTICIPATION = re.compile(
    r"\b(join(?:ed|ing)?|accept(?:ed|ance)?|participat(?:e|ing)|selected|batch member|"
    r"kabul\s+edildi)\b",
    re.IGNORECASE,
)
_NEGATIVE = re.compile(
    r"\b(not|never|didn['’]?t|wasn['’]?t|isn['’]?t)\b.{0,30}"
    r"\b(join|joining|joined|accepted|participating)\b",
    re.IGNORECASE,
)
_PERSON_BEFORE_JOIN = re.compile(
    r"\b([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})\s*"
    r"(?::|—|-)?\s*(?:is\s+|has\s+)?(?:joining|joined|accepted)",
)
_FOUNDER_PATTERN = re.compile(
    r"\b(?:[Ff]ounder|[Cc]o[- ]?[Ff]ounder)\s+"
    r"([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})",
)
_COMPANY_AFTER_WITH = re.compile(
    r"\b(?:joining|joined|accepted|participating)\b.{0,80}\bwith\s+"
    r"([A-Z][A-Za-z0-9&.'-]+(?:\s+[A-Z][A-Za-z0-9&.'-]+){0,3})"
    r"(?=\s+(?:in|at|for|to|this)\b|[,.!;:]|$)",
)
_VENTURE_SIGNAL = re.compile(
    r"\b(found(?:er|ed)|co[- ]?found(?:er|ed)|startup|stealth|launch(?:ed|ing)?|unveil(?:ed|ing)?|"
    r"raised?|funding|seed round|series [a-z]|invest(?:ed|ment)|portfolio company|"
    r"back(?:ed|ing)|venture|accelerator|join(?:ed|ing)|accepted|selected)\b",
    re.IGNORECASE,
)
_COMPANY_ACTION = re.compile(
    r"^(?P<company>[A-Z][A-Za-z0-9&.' -]{1,70}?)\s+"
    r"(?:launch(?:es|ed)?|unveil(?:s|ed)?|emerges?|debuts?|raises?|raised|secures?|secured)",
)
_PERSON_COMPANY_ACTION = re.compile(
    r"\b(?P<person>[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})"
    r"(?:,\s*(?:a\s+)?(?:former|ex)[^,.;]{1,60},)?\s+"
    r"(?:founded|co[- ]?founded|launched|started|is\s+building|unveiled)\s+"
    r"(?P<company>[A-Z][A-Za-z0-9&.'-]+(?:\s+[A-Z][A-Za-z0-9&.'-]+){0,3})"
    r"(?=[,.!;:]|\s+(?:after|with|to|in|for|and)\b|$)",
)
_PERSON_ROLE_AT_COMPANY = re.compile(
    r"\b(?P<person>[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})"
    r"(?:,|\s+is)\s+(?:the\s+)?(?:(?:CEO|COO|CTO)\s+(?:and|&)\s+)?"
    r"(?:[Ff]ounder|[Cc]o[- ]?[Ff]ounder)\s+(?:of|at)\s+"
    r"(?P<company>[A-Z][A-Za-z0-9&.'-]+(?:\s+[A-Z][A-Za-z0-9&.'-]+){0,3})"
    r"(?=[,.!;:]|$)",
)
_COMPANY_FOUNDED_BY = re.compile(
    r"\b(?P<company>[A-Z][A-Za-z0-9&.'-]+(?:\s+[A-Z][A-Za-z0-9&.'-]+){0,3})"
    r"\s+(?:was\s+)?(?:founded|co[- ]?founded|launched)\s+by\s+"
    r"(?P<person>[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})\b",
)
_FOUNDER_ROLE_BEFORE_PERSON = re.compile(
    r"\b(?:[Cc]o[- ]?)?[Ff]ounder(?:\s+(?:and|&)\s+(?:CEO|COO|CTO))?\s+"
    r"(?:of|at)\s+"
    r"(?P<company>[A-Z][A-Za-z0-9&.'-]+(?:\s+[A-Z][A-Za-z0-9&.'-]+){0,3})"
    r",\s+(?P<person>[A-Z][^\W\d_]*(?:['’.-][^\W\d_]+)*"
    r"(?:\s+[A-Z][^\W\d_]*(?:['’.-][^\W\d_]+)*){1,3})"
    r"(?=\s*[,;.]|\s+(?:said|says)\b|$)",
)
_COMPANY_ACCEPTED_INTO_PROGRAM_TR = re.compile(
    r"\b(?P<company>[A-Z][A-Za-z0-9&.'-]+(?:\s+[A-Z][A-Za-z0-9&.'-]+){0,3})"
    r"\s*,\s*[^.;]{0,100}\b(?:programına|dönemine)\s+kabul\s+edildi\b",
)
_NON_CANDIDATE_CONTEXT = re.compile(
    r"\b(?:quarterly earnings|stock price|market index|research paper|conference agenda|"
    r"venture fund (?:raises|closes)|fund size|macroeconomic|job listing|"
    r"definition\s*(?:&|and)\s*meaning|dictionary definition)\b",
    re.IGNORECASE,
)


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _investor_relationship(value: object) -> InvestorRelationship:
    try:
        return InvestorRelationship(str(value or "invested"))
    except ValueError:
        return InvestorRelationship.MENTIONED


def canonical_url(value: str) -> str:
    parts = urlsplit(value)
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith(("utm_", "trk", "fbclid", "gclid"))
    ]
    return urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path.rstrip("/") or "/",
            urlencode(query),
            "",
        )
    )


def stable_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def discovery_queries(program: ProgramConfig | None) -> list[str]:
    """Generic venture intents plus registry-derived program refinements."""
    generic = [
        'founder (launched OR founded OR "is building") startup announcement',
        'startup (launched OR "emerged from stealth" OR raised) founder',
    ]
    if program is None:
        return generic
    terms = [program.name, *program.keywords[:3]]
    aliases = " OR ".join(f'"{item}"' for item in dict.fromkeys(terms) if item)
    program_terms = f'({aliases}) "{program.batch}"'
    social_query = (
        "(site:linkedin.com/posts OR site:x.com OR site:instagram.com OR "
        f"site:threads.net) {program_terms} (joining OR founded OR launched) founder"
    )
    return [
        social_query,
        generic[0],
        generic[1],
        f"{program_terms} (joining OR accepted OR selected) founder startup",
        (
            f"(site:substack.com OR site:techcrunch.com) {program_terms} "
            "founder funding announcement"
        ),
        f"{program_terms} venture partner portfolio founder",
    ]


def verification_queries(
    program: ProgramConfig | None,
    candidate: CandidateHypothesis,
    *,
    include_directory: bool = True,
) -> list[str]:
    identity = candidate.candidate_name or candidate.company_name or candidate.display_name
    company = candidate.company_name or ""
    queries = [f'"{identity}" "{company}" (founder OR founded OR launched)']
    if program is not None:
        queries.extend(
            [
                f'"{identity}" "{company}" "{program.name}" "{program.batch}"',
                f'"{identity}" "{company}" (joined OR accepted OR participating)',
            ]
        )
    if program is not None and include_directory and program.directory_url:
        host = urlsplit(program.directory_url).hostname or ""
        queries.append(f'site:{host} "{company or identity}" "{program.batch}"')
    return queries


def enrichment_queries(candidate: CandidateHypothesis) -> list[str]:
    identity = candidate.candidate_name or candidate.display_name
    company = candidate.company_name or ""
    return [
        f'"{identity}" "{company}" official website founder',
        f'site:github.com OR site:huggingface.co "{identity}" "{company}"',
        f'site:arxiv.org OR site:orcid.org OR site:pypi.org "{identity}"',
        f'site:npmjs.com OR site:news.ycombinator.com "{identity}" "{company}"',
        f'site:web.archive.org "{company or identity}"',
    ]


def _metadata_roles(result: SearchResult) -> list[SourceTaxonomyRole]:
    raw = result.metadata.get("source_roles")
    if isinstance(raw, list):
        roles: list[SourceTaxonomyRole] = []
        for value in raw:
            try:
                roles.append(SourceTaxonomyRole(str(value).upper()))
            except ValueError:
                continue
        if roles:
            return list(dict.fromkeys(roles))
    domain = result.source_domain.casefold().removeprefix("www.")
    if domain == "web.archive.org":
        return [SourceTaxonomyRole.HISTORICAL]
    if domain in _RESTRICTED_SOCIAL:
        return [SourceTaxonomyRole.DISCOVERY]
    if domain in _TECHNICAL:
        return [SourceTaxonomyRole.IDENTITY, SourceTaxonomyRole.ENRICHMENT]
    if result.direct_public:
        return [SourceTaxonomyRole.IDENTITY, SourceTaxonomyRole.VERIFICATION]
    return [SourceTaxonomyRole.DISCOVERY]


def _candidate_names(
    result: SearchResult,
    hint: CandidateHypothesis | None,
    program: ProgramConfig | None,
) -> tuple[CandidateKind, str | None, str | None] | None:
    metadata = result.metadata
    person = str(metadata.get("candidate_name") or "").strip() or None
    company = str(metadata.get("company_name") or "").strip() or None
    kind_value = str(metadata.get("candidate_kind") or "").casefold()
    text = f"{result.title}; {result.snippet}"
    explicit = (
        _PERSON_COMPANY_ACTION.search(text)
        or _PERSON_ROLE_AT_COMPANY.search(text)
        or _COMPANY_FOUNDED_BY.search(text)
        or _FOUNDER_ROLE_BEFORE_PERSON.search(text)
    )
    if explicit:
        person = person or explicit.group("person").strip()
        company = company or explicit.group("company").strip(" :-.,;")
    if hint is not None:
        referenced = any(
            value and _norm(value) in _norm(text)
            for value in (hint.candidate_name, hint.company_name)
        )
        if not referenced:
            return None
        person = person or hint.candidate_name
        company = company or hint.company_name
        kind_value = hint.candidate_kind.value
    if person is None:
        match = _PERSON_BEFORE_JOIN.search(text) or _FOUNDER_PATTERN.search(text)
        person = match.group(1).strip() if match else None
    if company is None and person is not None:
        company_match = _COMPANY_AFTER_WITH.search(text)
        company = company_match.group(1).strip(" :-.,;") if company_match else None
    if company is None:
        company_match = _COMPANY_ACTION.search(result.title.strip())
        company = company_match.group("company").strip(" :-") if company_match else None
    if person and not is_probable_person_name(person):
        person = None
    if person is None and company is None and program is not None:
        aliases = "|".join(re.escape(value) for value in [program.name, *program.keywords])
        # A company-only announcement may open a provisional hypothesis, never
        # invent a founder. Ambiguous multiword subjects need other identity evidence.
        membership = re.search(
            rf"\b([A-Z][A-Za-z0-9&.'-]+)\s+has\s+joined\s+(?i:{aliases})(?!\w)",
            text,
        )
        if membership:
            company = membership.group(1)
        else:
            membership = _COMPANY_ACCEPTED_INTO_PROGRAM_TR.search(text)
            if membership:
                company = membership.group("company")
    if company and is_generic_label(company):
        company = None
    if not person and not company:
        return None
    kind = CandidateKind.PERSON if person or kind_value == "person" else CandidateKind.COMPANY
    return kind, person, company


def _program_and_stance(
    result: SearchResult,
    program: ProgramConfig | None,
    roles: list[SourceTaxonomyRole],
) -> ObservationStance | None:
    metadata = result.metadata
    metadata_program = str(metadata.get("program_id") or "")
    metadata_batch = str(metadata.get("batch_id") or "")
    if program is None:
        explicit = str(metadata.get("stance") or "").casefold()
        if explicit in {
            ObservationStance.CONTRADICTS.value,
            ObservationStance.AMBIGUOUS_IDENTITY.value,
            ObservationStance.STALE.value,
        }:
            return ObservationStance(explicit)
        return ObservationStance.NEUTRAL
    if metadata_program and metadata_program != program.id:
        return ObservationStance.OTHER_BATCH
    if metadata_batch and metadata_batch.casefold() != program.batch.casefold():
        return ObservationStance.OTHER_BATCH
    text = f"{result.title}; {result.snippet}"
    # Program aliases establish context, not a cohort. Use registry batch IDs
    # and names (not broad search keywords) with whole-token matching.
    batches = {
        batch.id.casefold()
        for batch in program.supported_batches
        if any(
            re.search(rf"(?<!\w){re.escape(label)}(?!\w)", text, re.IGNORECASE)
            for label in (batch.id, batch.name)
        )
    }
    if metadata_batch:
        batches.add(metadata_batch.casefold())
    if len(batches) > 1:
        return ObservationStance.AMBIGUOUS_IDENTITY
    if batches and program.batch.casefold() not in batches:
        return ObservationStance.OTHER_BATCH
    current_batch_match = program.batch.casefold() in batches
    explicit = metadata.get("stance")
    if explicit:
        try:
            stance = ObservationStance(str(explicit).casefold())
            if stance == ObservationStance.SUPPORTS and not current_batch_match:
                return ObservationStance.NEUTRAL
            return stance
        except ValueError:
            return None
    if str(metadata.get("temporal_status") or "").casefold() == "stale":
        return ObservationStance.STALE
    normalized = text.casefold()
    program_match = bool(
        metadata.get("batch_claim")
        or program.batch.casefold() in normalized
        or program.name.casefold() in normalized
        or any(keyword.casefold() in normalized for keyword in program.keywords)
    )
    if _NEGATIVE.search(text) and program_match:
        return ObservationStance.CONTRADICTS
    if program_match and current_batch_match and _PARTICIPATION.search(text):
        return ObservationStance.SUPPORTS
    if any(
        role in roles
        for role in (
            SourceTaxonomyRole.ENRICHMENT,
            SourceTaxonomyRole.IDENTITY,
            SourceTaxonomyRole.FUNDING,
            SourceTaxonomyRole.INVESTOR_SIGNAL,
            SourceTaxonomyRole.MARKET_SIGNAL,
            SourceTaxonomyRole.TEMPORAL_NEWS,
        )
    ):
        return ObservationStance.NEUTRAL
    # A credible venture/identity claim may enter the provisional hypothesis
    # layer without asserting accelerator membership. Program/batch support is
    # assessed separately and remains mandatory for canonical promotion.
    return ObservationStance.NEUTRAL


def observation_from_result(
    *,
    result: SearchResult,
    query: str,
    provider_id: str,
    run_id: str,
    program: ProgramConfig | None,
    observed_at: datetime,
    hint: CandidateHypothesis | None = None,
) -> BatchDiscoveryObservation | None:
    observation, _, _ = classify_result(
        result=result,
        query=query,
        provider_id=provider_id,
        run_id=run_id,
        program=program,
        observed_at=observed_at,
        hint=hint,
    )
    return observation


def classify_result(
    *,
    result: SearchResult,
    query: str,
    provider_id: str,
    run_id: str,
    program: ProgramConfig | None,
    observed_at: datetime,
    hint: CandidateHypothesis | None = None,
) -> tuple[BatchDiscoveryObservation | None, DiscoveryDropReason | None, bool]:
    """Classify one retrieved item once, retaining a deterministic drop reason."""
    extracted = extract_venture_metadata(
        result.title,
        result.snippet,
        roles=[role.value for role in _metadata_roles(result)],
    )
    result = result.model_copy(update={"metadata": {**extracted, **result.metadata}})
    text = f"{result.title}; {result.snippet}"
    metadata = result.metadata
    if len(text.strip()) < 12:
        return None, DiscoveryDropReason.INSUFFICIENT_CONTENT, False
    if _NON_CANDIDATE_CONTEXT.search(text) and not (
        metadata.get("candidate_name") or metadata.get("company_name")
    ):
        return None, DiscoveryDropReason.NOT_VENTURE_EVENT, False
    relevant = bool(
        _VENTURE_SIGNAL.search(text)
        or _COMPANY_ACCEPTED_INTO_PROGRAM_TR.search(text)
        or metadata.get("candidate_name")
        or metadata.get("company_name")
        or metadata.get("funding_claim_kind")
    )
    if not relevant:
        return None, DiscoveryDropReason.IRRELEVANT_TO_VENTURE, False
    names = _candidate_names(result, hint, program)
    if names is None:
        has_person_without_company = bool(
            metadata.get("candidate_name")
            or _PERSON_BEFORE_JOIN.search(text)
            or _FOUNDER_PATTERN.search(text)
        )
        reason = DiscoveryDropReason.ENTITY_ROLE_AMBIGUOUS if has_person_without_company else (
            DiscoveryDropReason.NO_ENTITY_MENTION
            if _VENTURE_SIGNAL.search(text)
            else DiscoveryDropReason.NO_CANDIDATE_CLAIM
        )
        return None, reason, True
    roles = _metadata_roles(result)
    stance = _program_and_stance(result, program, roles)
    if stance is None:
        return None, DiscoveryDropReason.OUTSIDE_TARGET_PROGRAM, True
    kind, person, company = names
    # Retain additional explicitly attributed founders of this same company.
    # Each relation must stand alone; names in neighboring cards or other
    # companies' claims are not cofounders, and do not add evidence origins.
    cofounders = [str(value) for value in metadata.get("cofounders", []) if str(value).strip()]
    if company:
        for match in _PERSON_ROLE_AT_COMPANY.finditer(text):
            name = match.group("person").strip()
            if (
                _norm(match.group("company")) == _norm(company)
                and _norm(name) != _norm(person)
                and is_probable_person_name(name)
                and not is_generic_label(name)
                and name not in cofounders
            ):
                cofounders.append(name)
    url = canonical_url(result.url)
    domain = (urlsplit(url).hostname or result.source_domain).casefold().removeprefix("www.")
    metadata = result.metadata
    referenced_source = str(metadata.get("referenced_source_url") or "").strip() or None
    origin_url = canonical_url(
        referenced_source or str(metadata.get("canonical_origin_url") or url)
    )
    origin_key = stable_digest(origin_url)
    claim_fingerprint = stable_digest(
        {
            "candidate": _norm(person or company),
            "company": _norm(company),
            "claim": _norm(result.snippet or result.title),
        }
    )
    restricted = domain in _RESTRICTED_SOCIAL
    default_reliability = (
        0.58
        if restricted
        else 0.88
        if result.direct_public
        else 0.72
        if domain in _TECHNICAL
        else 0.64
    )
    reliability = float(metadata.get("source_reliability") or default_reliability)
    identity_confidence = float(
        metadata.get("identity_confidence")
        or (0.9 if result.direct_public and (person or company) else 0.74)
    )
    directness = float(
        metadata.get("directness")
        or (0.92 if stance is ObservationStance.SUPPORTS and result.direct_public else 0.68)
    )
    temporal = float(
        metadata.get("temporal_relevance") or (0.45 if stance is ObservationStance.STALE else 0.9)
    )
    published_at = None
    if result.published_at:
        try:
            published_at = datetime.fromisoformat(result.published_at.replace("Z", "+00:00"))
        except ValueError:
            published_at = None
    identity = person or company or "candidate"
    observation_id = str(
        uuid.uuid5(
            _NAMESPACE,
            "|".join(
                (
            program.id if program else "generic_venture",
            program.batch if program else "unscoped",
                    url,
                    identity.casefold(),
                    stance.value,
                    ",".join(role.value for role in roles),
                )
            ),
        )
    )
    return BatchDiscoveryObservation(
        observation_id=observation_id,
        run_id=run_id,
        program_id=program.id if program else "generic_venture",
        batch_id=program.batch if program else "unscoped",
        candidate_kind=kind,
        candidate_name=person,
        company_name=company,
        claim=(result.snippet or result.title).strip()[:2_000],
        stance=stance,
        source_roles=roles,
        source_url=result.url,
        canonical_url=url,
        source_domain=domain,
        source_provider=provider_id,
        acquisition_providers=list(
            dict.fromkeys(
                [
                    *[
                        str(value)
                        for value in metadata.get("acquisition_providers", [])
                        if str(value).strip()
                    ],
                    provider_id,
                ]
            )
        ),
        query=query,
        origin_key=origin_key,
        origin_url=origin_url,
        publisher=str(metadata.get("publication") or domain).strip() or domain,
        claim_fingerprint=claim_fingerprint,
        referenced_source=canonical_url(referenced_source) if referenced_source else None,
        derivative_of=(origin_key if referenced_source else None),
        source_reliability=max(0.0, min(reliability, 1.0)),
        identity_confidence=max(0.0, min(identity_confidence, 1.0)),
        directness=max(0.0, min(directness, 1.0)),
        temporal_relevance=max(0.0, min(temporal, 1.0)),
        published_at=published_at,
        observed_at=observed_at,
        retrieved_at=observed_at,
        aliases=[str(value) for value in metadata.get("aliases", []) if str(value).strip()],
        usernames=[str(value) for value in metadata.get("usernames", []) if str(value).strip()],
        domains=[str(value) for value in metadata.get("domains", []) if str(value).strip()],
        cofounders=cofounders,
        provenance={
            **result.provenance,
            "provider": provider_id,
            "acquisition_provider": provider_id,
            "acquisition_providers": list(
                dict.fromkeys(
                    [
                        *[
                            str(value)
                            for value in metadata.get("acquisition_providers", [])
                            if str(value).strip()
                        ],
                        provider_id,
                    ]
                )
            ),
            "evidence_origin": canonical_url(origin_url),
            "claim_fingerprint": claim_fingerprint,
            "referenced_source": referenced_source,
            "raw_url": result.url,
            "access_mode": "direct_public" if result.direct_public else "search_discovery",
            "restricted_social_not_fetched": restricted,
        },
        raw_observation={
            "title": result.title,
            "snippet": result.snippet,
            "metadata": metadata,
        },
    ), None, True


def mark_source_independence(
    observations: list[BatchDiscoveryObservation],
) -> list[BatchDiscoveryObservation]:
    grouped: dict[str, list[BatchDiscoveryObservation]] = defaultdict(list)
    for item in observations:
        key = item.origin_key or stable_digest(item.origin_url or item.canonical_url)
        grouped[key].append(item)
    result: list[BatchDiscoveryObservation] = []
    for items in grouped.values():
        ordered = sorted(
            items,
            key=lambda item: (
                item.derivative_of is not None,
                SourceTaxonomyRole.VERIFICATION not in item.source_roles,
                -item.source_reliability,
                item.observation_id,
            ),
        )
        for index, item in enumerate(ordered):
            result.append(
                item.model_copy(
                    update={
                        "independence": (
                            SourceIndependence.INDEPENDENT
                            if index == 0 and not item.derivative_of
                            else SourceIndependence.DERIVATIVE
                        )
                    }
                )
            )
    return sorted(result, key=lambda item: (item.observed_at, item.observation_id))


def cluster_observations(
    observations: list[BatchDiscoveryObservation],
) -> dict[str, list[BatchDiscoveryObservation]]:
    """Cluster only on exact normalized identity/company keys—never fuzzy names."""
    person_clusters: dict[str, list[BatchDiscoveryObservation]] = defaultdict(list)
    company_only: dict[tuple[str, str], list[BatchDiscoveryObservation]] = defaultdict(list)
    for item in observations:
        company = _norm(item.company_name)
        person = _norm(item.candidate_name)
        if person:
            person_clusters[f"person:{person}|company:{company}"].append(item)
        elif company:
            identity_anchor = ",".join(sorted({_norm(value) for value in item.domains if value}))
            if not identity_anchor:
                identity_anchor = _norm(
                    item.raw_observation.get("metadata", {}).get("country")
                )
            company_only[(company, identity_anchor)].append(item)
    for (company, identity_anchor), items in company_only.items():
        matching = [key for key in person_clusters if key.endswith(f"company:{company}")]
        if identity_anchor and matching:
            matching = [
                key
                for key in matching
                if identity_anchor
                in {
                    *{
                        _norm(value)
                        for item in person_clusters[key]
                        for value in item.domains
                        if value
                    },
                    *{
                        _norm(item.raw_observation.get("metadata", {}).get("country"))
                        for item in person_clusters[key]
                    },
                }
            ]
        if len(matching) == 1:
            person_clusters[matching[0]].extend(items)
        else:
            suffix = f"|anchor:{identity_anchor}" if identity_anchor else ""
            person_clusters[f"company:{company}{suffix}"].extend(items)
    # A rename/stealth transition joins histories only with explicit deterministic
    # continuity (a shared domain or a declared prior company alias). Same-looking
    # founder/company names alone never trigger a merge.
    keys = sorted(person_clusters)
    consumed: set[str] = set()
    merged: dict[str, list[BatchDiscoveryObservation]] = {}
    for key in keys:
        if key in consumed:
            continue
        items = list(person_clusters[key])
        person_key = key.split("|company:", 1)[0]
        for other in keys:
            if other == key or other in consumed or not other.startswith(f"{person_key}|company:"):
                continue
            other_items = person_clusters[other]
            domains = {_norm(value) for item in items for value in item.domains if _norm(value)}
            other_domains = {
                _norm(value) for item in other_items for value in item.domains if _norm(value)
            }
            companies = {_norm(item.company_name) for item in items if item.company_name}
            other_companies = {
                _norm(item.company_name) for item in other_items if item.company_name
            }
            aliases = {_norm(value) for item in items for value in item.aliases if _norm(value)}
            other_aliases = {
                _norm(value) for item in other_items for value in item.aliases if _norm(value)
            }
            explicit_continuity = bool(domains & other_domains) or bool(
                companies & other_aliases or other_companies & aliases
            )
            if explicit_continuity:
                items.extend(other_items)
                consumed.add(other)
        merged[key] = items
    return merged


def _profile(items: list[BatchDiscoveryObservation]) -> CandidateProfile:
    founder = next((item.candidate_name for item in reversed(items) if item.candidate_name), None)
    company = next((item.company_name for item in reversed(items) if item.company_name), None)
    urls = [item.canonical_url for item in items]
    linkedin = next((url for url in urls if "linkedin.com/in/" in url), None)
    github = next((url for url in urls if "github.com/" in url), None)
    website = next(
        (
            url
            for item, url in zip(items, urls, strict=False)
            if SourceTaxonomyRole.IDENTITY in item.source_roles
            and item.source_domain not in _RESTRICTED_SOCIAL | _TECHNICAL
        ),
        None,
    )
    technical = sorted({url for url in urls if any(host in url for host in _TECHNICAL)})
    publications = sorted({url for url in urls if "arxiv.org" in url or "orcid.org" in url})
    background = sorted(
        {
            str(claim)
            for item in items
            for claim in item.raw_observation.get("metadata", {}).get("background_claims", [])
            if str(claim).strip()
        }
    )
    cofounders = sorted({name for item in items for name in item.cofounders})
    funding: list[FundingRoundEvidence] = []
    investor_signals: list[InvestorSignal] = []
    seen_funding: set[
        tuple[str, str | None, str | None, FundingClaimKind, FundingStatus]
    ] = set()
    seen_investors: set[tuple[str, str]] = set()
    for item in items:
        if item.independence is SourceIndependence.DERIVATIVE and any(
            other.origin_key == item.origin_key
            and other.independence is SourceIndependence.INDEPENDENT
            for other in items
        ):
            continue
        metadata = item.raw_observation.get("metadata", {})
        amount = str(metadata.get("funding_amount") or "").strip() or None
        round_name = str(metadata.get("funding_round") or "").strip() or None
        try:
            claim_kind = FundingClaimKind(str(metadata.get("funding_claim_kind") or "unknown"))
        except ValueError:
            claim_kind = FundingClaimKind.UNKNOWN
        try:
            funding_status = FundingStatus(str(metadata.get("funding_status") or "unknown"))
        except ValueError:
            funding_status = FundingStatus.UNKNOWN
        investors = [
            str(value).strip() for value in metadata.get("investors", []) if str(value).strip()
        ]
        lead = str(metadata.get("lead_investor") or "").strip() or None
        if amount or round_name or investors:
            key = (item.origin_key, amount, round_name, claim_kind, funding_status)
            if key not in seen_funding:
                seen_funding.add(key)
                funding.append(
                    FundingRoundEvidence(
                        amount=amount,
                        round=round_name,
                        claim_kind=claim_kind,
                        status=funding_status,
                        round_type=str(metadata.get("round_type") or "UNKNOWN"),
                        company_name=company,
                        claim=item.claim,
                        announced_at=item.published_at,
                        investors=investors,
                        lead_investor=lead,
                        source_url=item.canonical_url,
                        conflict_group=(
                            stable_digest({"company": _norm(company), "round": round_name})[:16]
                            if round_name and claim_kind is FundingClaimKind.ROUND_AMOUNT
                            else None
                        ),
                    )
                )
        for investor in investors:
            key = (investor.casefold(), item.origin_key)
            if key in seen_investors:
                continue
            seen_investors.add(key)
            investor_signals.append(
                InvestorSignal(
                    investor=investor,
                    partner=str(metadata.get("partner_name") or "").strip() or None,
                    relationship=(
                        InvestorRelationship.LED
                        if investor == lead
                        else _investor_relationship(metadata.get("investor_relationship"))
                    ),
                    source_url=item.canonical_url,
                    claim=item.claim,
                )
            )
    for left in funding:
        if not left.conflict_group:
            continue
        conflicts = [
            right.source_url
            for right in funding
            if right is not left
            and right.conflict_group == left.conflict_group
            and right.amount != left.amount
        ]
        left.conflicts_with = sorted(set(conflicts))
    technical_attention = min(20, len(technical) * 5 + len(publications) * 5)
    founder_attention = min(15, len(background) * 5)
    investor_attention = sum(
        {
            InvestorRelationship.LED: 15,
            InvestorRelationship.CO_LED: 15,
            InvestorRelationship.INVESTED: 12,
            InvestorRelationship.PARTICIPATED: 10,
            InvestorRelationship.PORTFOLIO_COMPANY: 10,
            InvestorRelationship.BACKED: 8,
            InvestorRelationship.MENTIONED: 2,
        }[signal.relationship]
        for signal in investor_signals
    )
    realized_funding = sum(
        claim.claim_kind in {FundingClaimKind.ROUND_AMOUNT, FundingClaimKind.TOTAL_FUNDING}
        and claim.status in {
            FundingStatus.ANNOUNCED,
            FundingStatus.CLOSED,
            FundingStatus.REPORTED,
        }
        for claim in funding
    )
    other_financial_attention = min(
        10,
        sum(
            claim.claim_kind in {FundingClaimKind.VALUATION, FundingClaimKind.REVENUE}
            for claim in funding
        )
        * 5,
    )
    attention = min(
        95,
        realized_funding * 35
        + other_financial_attention
        + investor_attention
        + technical_attention
        + founder_attention,
    )
    attention_label = (
        "high"
        if attention >= 70
        else "medium"
        if attention >= 40
        else "low"
        if attention
        else "none"
    )
    username = None
    if github:
        path = urlsplit(github).path.strip("/").split("/")
        username = path[0] if path else None
    fields = [founder, company, website, linkedin, github, technical, publications, background]
    completeness = round(100 * sum(bool(value) for value in fields) / len(fields))
    return CandidateProfile(
        founder_name=founder,
        company_name=company,
        website=website,
        linkedin_url=linkedin,
        github_url=github,
        github_username=username,
        technical_profiles=technical,
        publications=publications,
        background_claims=background,
        cofounders=cofounders,
        funding_rounds=[
            item for item in funding if item.claim_kind is FundingClaimKind.ROUND_AMOUNT
        ],
        financial_claims=funding,
        investor_signals=investor_signals,
        investment_attention_pct=attention,
        investment_attention_label=attention_label,
        completeness_pct=completeness,
    )


_COVERAGE_FAMILIES = (
    "official_program",
    "first_party",
    "public_search",
    "indexed_social",
    "techcrunch",
    "newsletter_substack",
    "vc_partner",
    "technical_publication",
    "historical_archive",
    "general_public_web",
)


def _coverage_family(item: BatchDiscoveryObservation) -> str:
    roles = set(item.source_roles)
    domain = item.source_domain
    if SourceTaxonomyRole.HISTORICAL in roles:
        return "historical_archive"
    if domain in _RESTRICTED_SOCIAL or "threads.net" in domain:
        return "indexed_social"
    if "techcrunch.com" in domain or item.source_provider == "techcrunch":
        return "techcrunch"
    if "substack.com" in domain or item.source_provider == "newsletter_watchlist":
        return "newsletter_substack"
    if item.source_provider == "vc_watchlist" or SourceTaxonomyRole.INVESTOR_SIGNAL in roles:
        return "vc_partner"
    if domain in _TECHNICAL:
        return "technical_publication"
    if SourceTaxonomyRole.VERIFICATION in roles and item.source_reliability >= 0.95:
        return "official_program"
    if item.raw_observation.get("metadata", {}).get("official_website"):
        return "first_party"
    if item.source_provider in {"duckduckgo", "bing", "yahoo", "brave"}:
        return "public_search"
    return "general_public_web"


def source_family_for_observation(item: BatchDiscoveryObservation) -> str:
    """Public source-family projection shared by coverage and yield diagnostics."""
    return _coverage_family(item)


def _source_coverage(
    items: list[BatchDiscoveryObservation], profile: CandidateProfile
) -> SourceCoverage:
    by_family: dict[str, list[BatchDiscoveryObservation]] = defaultdict(list)
    for item in items:
        by_family[_coverage_family(item)].append(item)
    families: list[SourceCoverageItem] = []
    technical_applicable = bool(
        profile.github_url
        or profile.technical_profiles
        or profile.publications
        or any(
            re.search(r"\b(ai|machine learning|research|developer|open source)\b", item.claim, re.I)
            for item in items
        )
    )
    historical_applicable = bool(profile.website or any(item.domains for item in items)) or any(
        SourceTaxonomyRole.HISTORICAL in item.source_roles for item in items
    )
    applicability = {
        "official_program": (True, "Applicable to every accelerator investigation."),
        "first_party": (
            True,
            "Applicable because identity should be checked against a first-party site.",
        ),
        "public_search": (True, "Applicable to every public discovery investigation."),
        "indexed_social": (True, "Applicable through permitted public search indexes only."),
        "techcrunch": (True, "Applicable for startup and funding reporting."),
        "newsletter_substack": (True, "Applicable for emerging-company announcements."),
        "vc_partner": (True, "Applicable for investment and portfolio corroboration."),
        "technical_publication": (
            technical_applicable,
            "Applicable because technical/research identity signals were detected."
            if technical_applicable
            else "Not applicable: no technical or publication identity detected.",
        ),
        "historical_archive": (
            historical_applicable,
            "Applicable because a public site or historical signal is available."
            if historical_applicable
            else "Not applicable: no website or historical identity is available.",
        ),
        "general_public_web": (True, "Applicable to every public identity investigation."),
    }
    for family in _COVERAGE_FAMILIES:
        observations = by_family.get(family, [])
        applicable, reason = applicability[family]
        supporting = sum(item.stance is ObservationStance.SUPPORTS for item in observations)
        contradicting = sum(
            item.stance in {ObservationStance.CONTRADICTS, ObservationStance.OTHER_BATCH}
            for item in observations
        )
        status = (
            "supporting"
            if supporting
            else "contradicting"
            if contradicting
            else "checked_no_evidence"
            if observations
            else "not_checked"
        )
        if not applicable:
            status = "not_applicable"
        families.append(
            SourceCoverageItem(
                family=family,
                status=status,
                applicable=applicable,
                applicability_reason=reason,
                supporting=supporting,
                contradicting=contradicting,
                checked_no_evidence=bool(observations and not supporting and not contradicting),
            )
        )
    applicable_count = sum(item.applicable for item in families)
    checked = sum(
        item.applicable and item.status in {"supporting", "contradicting", "checked_no_evidence"}
        for item in families
    )
    return SourceCoverage(
        applicable_families=applicable_count,
        checked_families=checked,
        supporting_families=sum(item.supporting > 0 for item in families),
        contradicting_families=sum(item.contradicting > 0 for item in families),
        coverage_pct=round(100 * checked / applicable_count, 2),
        families=families,
    )


def assess(
    items: list[BatchDiscoveryObservation],
) -> tuple[BatchParticipationAssessment, CandidateState]:
    support = [item for item in items if item.stance is ObservationStance.SUPPORTS]
    contradictions = [item for item in items if item.stance is ObservationStance.CONTRADICTS]
    other_batch = [item for item in items if item.stance is ObservationStance.OTHER_BATCH]
    ambiguous = [item for item in items if item.stance is ObservationStance.AMBIGUOUS_IDENTITY]
    stale = [item for item in items if item.stance is ObservationStance.STALE]
    derivative = [item for item in items if item.independence is SourceIndependence.DERIVATIVE]
    independent_support = [
        item for item in support if item.independence is SourceIndependence.INDEPENDENT
    ]
    support_effects = [
        item.source_reliability
        * item.identity_confidence
        * item.directness
        * item.temporal_relevance
        for item in independent_support
    ]
    support_strength = 1.0 - math.prod(1.0 - effect for effect in support_effects)
    negative_effects = [
        item.source_reliability * item.identity_confidence * item.directness
        for item in [*contradictions, *other_batch]
        if item.independence is not SourceIndependence.DERIVATIVE
    ]
    negative_strength = 1.0 - math.prod(1.0 - effect for effect in negative_effects)
    identity_confidence = max((item.identity_confidence for item in items), default=0.0)
    raw = support_strength * identity_confidence
    if len(independent_support) >= 2:
        raw += 0.08
    raw -= negative_strength * 0.58
    raw -= min(0.18, 0.05 * len(ambiguous))
    if len(independent_support) < 2:
        raw = min(raw, 0.64)
    confidence = max(0.0, min(raw, 0.95))
    confidence_pct = round(confidence * 100)
    identity_pct = round(identity_confidence * 100)
    blockers: list[str] = []
    if len(independent_support) < 2:
        blockers.append("fewer_than_two_independent_supporting_sources")
    if identity_confidence < 0.8:
        blockers.append("identity_confidence_below_80_pct")
    if confidence_pct < 72:
        blockers.append("batch_confidence_below_72_pct")
    if negative_strength >= support_strength and negative_effects:
        blockers.append("contradictory_or_other_batch_evidence_dominates")
    profile = _profile(items)
    if not profile.company_name:
        blockers.append("company_identity_missing")
    batch_evidence_present = bool(support)
    if not batch_evidence_present:
        blockers.append("no_target_program_or_batch_evidence")
    promotion = not blockers
    official = any(
        SourceTaxonomyRole.VERIFICATION in item.source_roles
        and item.source_reliability >= 0.95
        and item.stance is ObservationStance.SUPPORTS
        for item in independent_support
    )
    if (contradictions or other_batch) and (not support or negative_strength >= support_strength):
        state = CandidateState.REJECTED
    elif promotion and official and confidence_pct >= 85:
        state = CandidateState.CONFIRMED
    elif promotion and confidence_pct >= 80:
        state = CandidateState.HIGH_CONFIDENCE
    elif promotion:
        state = CandidateState.CORROBORATED
    elif confidence_pct >= 40:
        state = CandidateState.SUSPECTED
    elif support:
        state = CandidateState.DISCOVERED
    else:
        state = CandidateState.UNCERTAIN
    components = [
        ConfidenceComponent(
            component="independent_support",
            effect=round(support_strength, 4),
            explanation="Independent origins combine; derivative copies count once.",
            observation_ids=[item.observation_id for item in independent_support],
        ),
        ConfidenceComponent(
            component="identity",
            effect=round(identity_confidence, 4),
            explanation="Exact names, usernames, domains, and identity-bearing pages only.",
            observation_ids=[item.observation_id for item in items],
        ),
        ConfidenceComponent(
            component="contradictions",
            effect=round(-negative_strength, 4),
            explanation="Contradictory and other-batch claims reduce confidence.",
            observation_ids=[item.observation_id for item in [*contradictions, *other_batch]],
        ),
    ]
    identity_corroborated = identity_confidence >= 0.8 and len(
        {
            item.origin_key
            for item in items
            if item.independence is SourceIndependence.INDEPENDENT
            and SourceTaxonomyRole.IDENTITY in item.source_roles
        }
    ) >= 1
    coverage = _source_coverage(items, profile)
    coverage_label = (
        "HIGH CONFIDENCE · LOW COVERAGE"
        if confidence_pct >= 72 and coverage.coverage_pct < 50
        else "LOW CONFIDENCE · HIGH COVERAGE"
        if confidence_pct < 50 and coverage.coverage_pct >= 75
        else "ADEQUATE COVERAGE"
        if coverage.coverage_pct >= 50
        else "LOW COVERAGE"
    )
    next_best_checks = [
        f"Check {item.family}: {item.applicability_reason}"
        for item in coverage.families
        if item.applicable and item.status == "not_checked"
    ][:3]
    return (
        BatchParticipationAssessment(
            confidence_pct=confidence_pct,
            identity_confidence_pct=identity_pct,
            independent_supporting_sources=len(independent_support),
            supporting_observations=len(support),
            contradictory_observations=len(contradictions),
            other_batch_observations=len(other_batch),
            ambiguous_observations=len(ambiguous),
            stale_observations=len(stale),
            derivative_observations=len(derivative),
            source_diversity=sorted({item.source_domain for item in independent_support}),
            components=components,
            promotion_eligible=promotion,
            promotion_blockers=blockers,
            identity_corroborated=identity_corroborated,
            batch_evidence_present=batch_evidence_present,
            accelerator_investigation_state=(
                "batch_evidence_present"
                if batch_evidence_present
                else "needs_accelerator_investigation"
            ),
            coverage_label=coverage_label,
            next_best_checks=next_best_checks,
            source_coverage=coverage,
        ),
        state,
    )


def build_hypotheses(
    observations: list[BatchDiscoveryObservation],
    *,
    run_id: str,
    program: ProgramConfig | None,
    revision_for: Callable[[str], int],
) -> list[CandidateHypothesis]:
    hypotheses: list[CandidateHypothesis] = []
    for cluster_key, items in sorted(cluster_observations(observations).items()):
        items = sorted(items, key=lambda item: (item.observed_at, item.observation_id))
        assessment, state = assess(items)
        person = next(
            (item.candidate_name for item in reversed(items) if item.candidate_name),
            None,
        )
        company = next((item.company_name for item in reversed(items) if item.company_name), None)
        kind = CandidateKind.PERSON if person else CandidateKind.COMPANY
        display_name = person or company or "Unknown candidate"
        scope_id = program.id if program else "generic_venture"
        scope_batch = program.batch if program else "unscoped"
        hypothesis_id = str(uuid.uuid5(_NAMESPACE, f"{scope_id}|{scope_batch}|{cluster_key}"))
        input_digest = stable_digest([item.model_dump(mode="json") for item in items])
        revision = int(revision_for(hypothesis_id))
        prior_origins = {
            item.origin_key for item in items if item.run_id != run_id
        }
        triggering = [
            item for item in items if item.run_id == run_id and item.origin_key not in prior_origins
        ]
        why_now = [
            "New independent "
            f"{','.join(role.value for role in item.source_roles)} evidence from "
            f"{item.publisher or item.source_domain}."
            for item in triggering
            if item.independence is SourceIndependence.INDEPENDENT
        ][:4]
        hypotheses.append(
            CandidateHypothesis(
                hypothesis_id=hypothesis_id,
                revision_id=str(uuid.uuid5(_NAMESPACE, f"{hypothesis_id}|{input_digest}")),
                revision=revision,
                run_id=run_id,
                program_id=scope_id,
                batch_id=scope_batch,
                candidate_kind=kind,
                display_name=display_name,
                candidate_name=person,
                company_name=company,
                aliases=sorted(
                    {
                        *{alias for item in items for alias in item.aliases},
                        *{
                            value
                            for item in items
                            if (value := item.company_name) and value != company
                        },
                    }
                ),
                usernames=sorted({name for item in items for name in item.usernames}),
                domains=sorted({domain for item in items for domain in item.domains}),
                urls=sorted({item.canonical_url for item in items}),
                cofounders=sorted({name for item in items for name in item.cofounders}),
                observation_ids=[item.observation_id for item in items],
                state=state,
                assessment=assessment,
                profile=_profile(items),
                first_seen_at=min(item.observed_at for item in items),
                last_updated_at=max(item.observed_at for item in items),
                input_digest=input_digest,
                why_now=why_now
                or ["Existing evidence was re-evaluated; no new independent origin."],
                provenance={
                    "cluster_key": cluster_key,
                    "clustering": "exact_normalized_identity_company_username_domain",
                    "query_strings_are_not_identity_evidence": True,
                },
            )
        )
    return hypotheses


def source_entity_id(program: ProgramConfig, kind: str, name: str) -> str:
    return f"batch_discovery__{program.id}_{kind}_{_slug(name)}"
