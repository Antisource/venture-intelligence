import { lazy, Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";

import type { BatchDiscoveryObservation, EntityIntelligenceResponse } from "../../api/client";
import { batchCandidateQuery, decisionQuery, entityIntelligenceQuery } from "../../api/queries";
import { EmptyState, ErrorState, LoadingState } from "../../components/AsyncState";
import { DecisionDetails } from "../decisions/DecisionPages";
import { IntelligenceLineage } from "../intelligence/IntelligencePages";
import { ContextualAnalyst } from "../operations/OperationsPages";
import { evidenceState, researchLink, words } from "../ProductWorkspaces";
import { ResearchCollector } from "./ResearchPage";
import { KnowledgeEvidence } from "./KnowledgeEvidence";

const Graph = lazy(() => import("../graph/GraphPage"));
const views = ["overview", "evidence", "timeline", "network", "decisions", "investigate", "assistant"] as const;

export function ResearchWorkspace() {
  const { entityId = "" } = useParams();
  const [parameters, setParameters] = useSearchParams();
  const candidateId = parameters.get("candidate") ?? "";
  const requested = parameters.get("view") ?? (entityId || candidateId ? "overview" : "investigate");
  const view = views.some((v) => v === requested) ? requested : "overview";
  const intelligence = useQuery({ ...entityIntelligenceQuery(entityId), refetchInterval: 15000 });
  const candidate = useQuery({ ...batchCandidateQuery(candidateId), refetchInterval: 15000 });
  const decision = useQuery({ ...decisionQuery(entityId), enabled: Boolean(entityId) && view === "decisions" });
  const data = intelligence.data;
  const hypothesis = candidate.data?.candidate;
  const name = data?.entity.canonical_name ?? hypothesis?.display_name;
  const observations = candidate.data?.observations ?? [];
  const attachedEvidence = (data?.evidence ?? []).filter((item) => item.attribution_status === "attached");
  const unboundEvidence = (data?.evidence ?? []).filter((item) => item.attribution_status === "unbound");
  const contradictoryEvidence = (data?.evidence ?? []).filter((item) => item.attribution_status === "contradictory");
  const contradictoryObservations = observations.filter((item) => ["contradicts", "other_batch"].includes(item.stance));
  const unboundObservations = observations.filter((item) => !["contradicts", "other_batch"].includes(item.stance));
  const promotedId = hypothesis?.canonical_company_id ?? hypothesis?.canonical_person_id;
  const isCandidate = Boolean(candidateId);
  return <section className="product-workspace research-workspace">
    <header className="workspace-heading"><div><Link to="/discover" className="muted">← Discover</Link><h1>{name ?? "Research"}</h1><p>{data ? `Identity ${words(data.entity.identity_status)} · Program ${words(data.entity.program_status)} · ${data.entity.independent_evidence_origins} independent evidence origins` : hypothesis ? evidenceState(hypothesis) : "Follow a question through evidence to a decision."}</p></div>{entityId ? <Link to={`${researchLink(entityId)}?view=decisions`}>Track / follow up →</Link> : null}</header>
    {intelligence.isLoading || candidate.isLoading ? <LoadingState /> : null}
    {intelligence.error || candidate.error ? <ErrorState error={intelligence.error ?? candidate.error} /> : null}
    <div className="workspace-tabs" aria-label="Research views">{views.map((id) => <button aria-pressed={view === id} className={view === id ? "selected" : "secondary-button"} key={id} onClick={() => { const next = new URLSearchParams(parameters); next.set("view", id); setParameters(next); }}>{id === "network" ? "Network / relationships" : id === "decisions" ? "Notes / decisions" : words(id)}</button>)}</div>
    {view === "overview" ? <>
      {data ? <IntelligenceLineage key={entityId} data={data} /> : null}
      {hypothesis ? <div className="research-summary">
        <section><h2>Why surfaced</h2><p>{hypothesis.why_now?.join(" ") || "Public signals require further investigation."}</p><p>Identity {hypothesis.assessment.identity_corroborated ? "corroborated" : hypothesis.assessment.ambiguous_observations ? "ambiguous" : "provisional"} · Program {words(evidenceState(hypothesis))}</p><p>{hypothesis.assessment.independent_supporting_sources} independent supporting origins; {hypothesis.assessment.contradictory_observations} contradictory observations.</p>{hypothesis.profile.investment_attention_pct != null ? <p>Attention score {hypothesis.profile.investment_attention_pct}%</p> : null}</section>
        <section><h2>What remains uncertain</h2><p>{hypothesis.assessment.promotion_blockers?.map(words).join("; ") || "Review the supporting observations and identity match."}</p>{!promotedId ? <strong>Insufficient evidence — candidate not promoted</strong> : <Link to={researchLink(promotedId)}>Open validated identity →</Link>}</section>
        <section><h2>Intelligence profile</h2><dl className="compact-facts"><div><dt>Founder</dt><dd>{hypothesis.candidate_name ?? "Unknown / insufficient public evidence"}</dd></div><div><dt>Company</dt><dd>{hypothesis.company_name ?? "Unknown / insufficient public evidence"}</dd></div><div><dt>Website</dt><dd>{hypothesis.profile.website ? <a href={hypothesis.profile.website} target="_blank" rel="noreferrer">{hypothesis.profile.website}</a> : "Not established"}</dd></div><div><dt>Background</dt><dd>{hypothesis.profile.background_claims?.join(" · ") || "Unknown / insufficient public evidence"}</dd></div><div><dt>Cofounders</dt><dd>{hypothesis.profile.cofounders?.join(" · ") || "Not established"}</dd></div></dl>
          {hypothesis.profile.funding_rounds?.map((item) => <p key={item.source_url}><a href={item.source_url} target="_blank" rel="noreferrer">{item.amount ?? "Amount undisclosed"} · {item.round ?? "Round unspecified"}</a></p>)}
          {hypothesis.profile.investor_signals?.map((item) => <p key={`${item.investor}-${item.source_url}`}><a href={item.source_url} target="_blank" rel="noreferrer">{item.investor}{item.partner ? ` · ${item.partner}` : ""} · {words(item.relationship)}</a></p>)}
        </section>
      </div> : !entityId ? <EmptyState title="Choose an opportunity or investigate a target" detail="Discover retains the context for your research." /> : null}
    </> : null}
    {view === "evidence" ? <>
      {data ? <KnowledgeEvidence entityId={data.entity.entity_id} entityType={data.entity.entity_type} /> : null}
      <EvidenceGroup title="Canonical / attached evidence" detail="Evidence confidently attributed to this identity." items={attachedEvidence} />
      <EvidenceGroup title="Ambiguous / unbound leads" detail="Potentially relevant leads excluded from identity confidence and ranking." items={unboundEvidence} />
      <ObservationGroup title="Ambiguous / unbound leads" detail="Candidate observations remain outside the canonical identity until corroborated." items={unboundObservations} />
      <EvidenceGroup title="Contradictory evidence" detail="Evidence that conflicts with identity or program claims." items={contradictoryEvidence} />
      <ObservationGroup title="Contradictory evidence" detail="Contradictory and other-batch observations do not promote this candidate." items={contradictoryObservations} />
      {!observations.length && !data?.evidence.length ? <EmptyState title="No evidence available" detail="No identity or historical claim is inferred from this absence." /> : null}
    </> : null}
    {view === "timeline" ? <section className="research-evidence"><h2>Observation timeline</h2>
      {(data?.evidence ?? []).slice().sort((a, b) => (b.observed_at ?? "").localeCompare(a.observed_at ?? "")).map((item) => <article key={item.evidence_id}><h3>{item.claim}</h3><p>{words(item.attribution_status)} · Observed {item.observed_at ? new Date(item.observed_at).toLocaleString() : "Unknown time"}{item.occurred_at ? ` · Event time ${new Date(item.occurred_at).toLocaleString()}` : ""}</p></article>)}
      {observations.slice().sort((a, b) => (b.observed_at ?? "").localeCompare(a.observed_at ?? "")).map((item) => <article key={item.observation_id}><h3>{item.claim}</h3><p>{words(item.stance)} · {item.temporal_relevance < 0.5 ? "Historical relevance uncertain" : "Public observation"} · Observed {item.observed_at ? new Date(item.observed_at).toLocaleString() : "Unknown time"}</p></article>)}
    </section> : null}
    {view === "network" ? isCandidate && !promotedId ? <EmptyState title="Insufficient evidence — candidate not promoted" detail="Unverified identities do not enter the canonical relationship graph." /> : <Suspense fallback={<LoadingState label="Loading relationships…" />}><Graph key={entityId || promotedId || "all"} focus={entityId || promotedId || undefined} /></Suspense> : null}
    {view === "decisions" ? <>{decision.isLoading ? <LoadingState /> : null}{decision.error ? <ErrorState error={decision.error} /> : null}{decision.data ? <DecisionDetails key={entityId} data={decision.data} /> : !entityId ? <EmptyState title="A validated identity is required" detail="Unverified candidates cannot enter Portfolio. Inspect evidence and corroborate the identity first." /> : null}</> : null}
    {view === "investigate" ? <ResearchCollector key={name ?? "new"} initialName={data?.entity.canonical_name ?? hypothesis?.candidate_name ?? hypothesis?.company_name ?? ""} initialType={data?.entity.entity_type ?? (hypothesis?.candidate_name ? "person" : "company")} /> : null}
    {view === "assistant" ? <ContextualAnalyst key={name ?? "new"} contextName={name} /> : null}
  </section>;
}

function EvidenceGroup({ title, detail, items }: { title: string; detail: string; items: EntityIntelligenceResponse["evidence"] }) {
  if (!items.length) return null;
  return <section className="research-evidence"><h2>{title}</h2><p className="muted">{detail}</p>{items.map((item) => <article key={item.evidence_id}><h3>{item.claim}</h3><p>{Math.round(item.confidence * 100)}% evidence strength · Observed {item.observed_at ? new Date(item.observed_at).toLocaleString() : "Unknown time"}</p><p>{item.evidence_origin_url ?? "Evidence origin unavailable"}{item.acquisition_providers?.length ? ` · Acquired via ${item.acquisition_providers.join(", ")}` : ""}</p>{item.source_urls?.map((url) => <a key={url} href={url} target="_blank" rel="noreferrer">Inspect source ↗</a>)}<details><summary>Provenance</summary><pre>{JSON.stringify(item, null, 2)}</pre></details></article>)}</section>;
}

function ObservationGroup({ title, detail, items }: { title: string; detail: string; items: BatchDiscoveryObservation[] }) {
  if (!items.length) return null;
  return <section className="research-evidence"><h2>{title}</h2><p className="muted">{detail}</p>{items.map((item) => <article key={item.observation_id}><h3>{item.claim}</h3><p>{words(item.stance)} · Observed {item.observed_at ? new Date(item.observed_at).toLocaleString() : "Unknown time"}</p><a href={item.canonical_url} target="_blank" rel="noreferrer">{item.source_domain} ↗</a><details><summary>Provenance and confidence basis</summary><pre>{JSON.stringify(item, null, 2)}</pre></details></article>)}</section>;
}
