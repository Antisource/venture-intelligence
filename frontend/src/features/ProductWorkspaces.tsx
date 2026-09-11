import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { apiClient, type CandidateHypothesis } from "../api/client.ts";
import { alertsQuery, batchDiscoveryQuery, cyclesQuery, decisionsQuery, rankingsQuery } from "../api/queries";
import { EmptyState, ErrorState, LoadingState } from "../components/AsyncState";
import { usePrograms } from "../state/ProgramProvider";

export const words = (value: string) => value.replaceAll("_", " ");
export const researchLink = (id: string) => `/research/${encodeURIComponent(id)}`;
export const candidateLink = (id: string) => `/research?candidate=${encodeURIComponent(id)}`;
export function evidenceState(item: CandidateHypothesis) {
  if (item.state === "rejected" || item.review_state === "dismissed") return "Dismissed";
  if (item.assessment.contradictory_observations) return "Conflicting evidence";
  return item.assessment.promotion_eligible ? "Corroborated" : "Needs corroboration";
}

function entityReason(item: Awaited<ReturnType<typeof apiClient.rankings>>["items"][number]) {
  if (item.entity.program_status === "verified" && item.entity.batch_id) {
    return `Verified ${item.entity.batch_id} participant`;
  }
  const feature = item.top_feature?.name?.replaceAll("_", " ");
  if (feature) return `${feature} supported by canonical evidence`;
  if (item.entity.independent_evidence_origins) {
    return `${item.entity.independent_evidence_origins} independent evidence origins support this identity`;
  }
  return "Evidence-backed opportunity ready for analyst review";
}

function entityState(item: Awaited<ReturnType<typeof apiClient.rankings>>["items"][number]) {
  return `Identity ${words(item.entity.identity_status)} · Program ${words(item.entity.program_status)} · ${item.entity.independent_evidence_origins} independent origins`;
}

export function uniqueCanonicalRankings(
  items: Awaited<ReturnType<typeof apiClient.rankings>>["items"],
) {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (seen.has(item.entity.entity_id)) return false;
    seen.add(item.entity.entity_id);
    return true;
  });
}

export function uniqueCanonicalCompanyLeads(items: CandidateHypothesis[]) {
  const seen = new Set<string>();
  return items.filter((item) => {
    const normalizedCompany = item.company_name?.trim().toLocaleLowerCase();
    const identity = item.canonical_company_id
      ? `canonical:${item.canonical_company_id}`
      : normalizedCompany
        ? `provisional:${normalizedCompany}`
        : `hypothesis:${item.hypothesis_id}`;
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

export function uniqueCanonicalPersonLeads(items: CandidateHypothesis[]) {
  const seen = new Set<string>();
  return items.filter((item) => {
    const normalizedPerson = item.candidate_name?.trim().toLocaleLowerCase();
    const identity = item.canonical_person_id
      ? `canonical:${item.canonical_person_id}`
      : normalizedPerson
        ? `provisional:${normalizedPerson}`
        : `hypothesis:${item.hypothesis_id}`;
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

function useCandidates() {
  const programs = usePrograms();
  const ids = programs.selectedProgram ? [programs.selectedProgram] : [...programs.programs.map((p) => p.id), null];
  const queries = useQueries({ queries: ids.map((id) => batchDiscoveryQuery(id)) });
  return { items: queries.flatMap((q, index) => {
    const programId = ids[index] ?? "generic_venture";
    const expectedBatch = programs.programs.find((program) => program.id === programId)?.batch;
    return (q.data?.candidates ?? []).filter((item) => item.program_id === programId && (!expectedBatch || item.batch_id === expectedBatch));
  }), loading: programs.isLoading || queries.some((q) => q.isLoading), error: programs.error ?? queries.find((q) => q.error)?.error };
}

export function InboxPage() {
  const programs = usePrograms();
  const candidates = useCandidates();
  const ranking = useQuery(rankingsQuery(undefined, programs.selectedProgram || undefined));
  const decisions = useQuery(decisionsQuery(programs.selectedProgram || undefined));
  const cycles = useQuery(cyclesQuery());
  const alerts = useQuery(alertsQuery());
  const [view, setView] = useState("attention");
  const changes = (cycles.data?.cycles ?? []).flatMap((c) => c.program_results ?? []).filter((p) => !programs.selectedProgram || p.program_id === programs.selectedProgram).flatMap((p) => p.changes ?? []);
  const changed = new Map([...changes].reverse().map((c) => [c.hypothesis_id, c]));
  const leads = candidates.items.filter((c) => c.state !== "rejected" && c.review_state !== "dismissed" && (view === "contradictions" ? c.assessment.contradictory_observations > 0 : view === "changes" ? changed.has(c.hypothesis_id) : true));
  const tracked = Object.values(decisions.data?.stages ?? {}).flat();
  return <section className="product-workspace">
    <header className="workspace-heading"><div><h1>Inbox</h1><p>What deserves your attention.</p></div><Link to="/discover">Explore intelligence →</Link></header>
    <div className="workspace-tabs" aria-label="Inbox views">{[["attention", "Needs attention"], ["changes", "Material changes"], ["contradictions", "Conflicting evidence"], ["watching", "Watching"]].map(([id, label]) => <button className={view === id ? "selected" : "secondary-button"} key={id} onClick={() => setView(id ?? "attention")}>{label}</button>)}</div>
    {candidates.loading || ranking.isLoading ? <LoadingState /> : null}
    {candidates.error ? <ErrorState error={candidates.error} /> : null}
    <div className="intelligence-feed">
      {view === "attention" ? ranking.data?.items.map((item) => <Link className="feed-item" key={item.entity.entity_id} to={researchLink(item.entity.entity_id)}><span className="feed-rank">{item.ranking.rank}</span><div><h2>{item.entity.canonical_name}</h2><p>{entityReason(item)}</p><small>{entityState(item)} · Attention score {Math.round(item.ranking.composite_score * 100)}%</small></div><span>Review →</span></Link>) : null}
      {view !== "watching" ? leads.map((item) => <Link className="feed-item" key={item.hypothesis_id} to={candidateLink(item.hypothesis_id)}><div><h2>{item.display_name}</h2><p>{changed.get(item.hypothesis_id)?.explanation ?? item.why_now?.[0] ?? "New public evidence to investigate"}</p><small>{evidenceState(item)} · {Math.round(item.assessment.confidence_pct)}% program confidence</small><p className="muted">{item.assessment.promotion_blockers?.[0] ? words(item.assessment.promotion_blockers[0]) : "Inspect supporting evidence"}</p></div><span>Investigate →</span></Link>) : tracked.map((item) => <Link className="feed-item" key={item.entity.entity_id} to={researchLink(item.entity.entity_id)}><div><h2>{item.entity.canonical_name}</h2><p>{item.recommendation?.reasoning ?? item.crm.notes?.at(-1) ?? "Awaiting your next review"}</p><small>{words(item.crm.stage)}</small></div><span>Follow up →</span></Link>)}
      {view === "attention" ? alerts.data?.alerts.map((alert, i) => <article className="feed-item" key={alert.alert_id ?? i}><div><h2>{alert.entity_name ?? "Intelligence alert"}</h2><p>{alert.reasoning}</p><small>{alert.fired_at ? new Date(alert.fired_at).toLocaleString() : "New update"}</small><Link to="/portfolio">Review tracked opportunities →</Link></div></article>) : null}
    </div>
    {!candidates.loading && !leads.length && !(view === "watching" ? tracked.length : view === "attention" ? ranking.data?.items.length || alerts.data?.alerts.length : false) ? <EmptyState title="Nothing needs attention here" detail="New evidence, material changes, and follow-ups will appear here. Explore Discover to find your next investigation." /> : null}
    {ranking.error || decisions.error || alerts.error || cycles.error ? <p role="status">Some attention items are temporarily unavailable. Available intelligence is preserved.</p> : null}
  </section>;
}

export function DiscoverPage() {
  const [parameters, setParameters] = useSearchParams();
  const tab = parameters.get("type") ?? "companies";
  const [search, setSearch] = useState(parameters.get("q") ?? "");
  const [state, setState] = useState("all");
  const [sort, setSort] = useState("priority");
  const programs = usePrograms();
  const candidates = useCandidates();
  const rankings = useQuery(rankingsQuery(undefined, programs.selectedProgram || undefined));
  const client = useQueryClient();
  const refresh = useMutation({ mutationFn: () => apiClient.runCycle(programs.selectedProgram ? [programs.selectedProgram] : undefined), onSuccess: () => client.invalidateQueries() });
  const type = tab === "founders" ? "person" : "company";
  const entities = uniqueCanonicalRankings(rankings.data?.items ?? []).filter((i) => i.entity.entity_type === type && i.entity.canonical_name.toLowerCase().includes(search.toLowerCase()) && ["all", "validated"].includes(state));
  const ids = new Set(entities.map((i) => i.entity.entity_id));
  const filteredLeads = candidates.items.filter((i) => i.state !== "rejected" && i.review_state !== "dismissed" && (type === "person" ? i.candidate_name : i.company_name) && !ids.has((type === "person" ? i.canonical_person_id : i.canonical_company_id) ?? "") && i.display_name.toLowerCase().includes(search.toLowerCase()) && (state === "all" || state === "uncertain"));
  const leads = type === "company"
    ? uniqueCanonicalCompanyLeads(filteredLeads)
    : uniqueCanonicalPersonLeads(filteredLeads);
  if (sort === "name") { entities.sort((a, b) => a.entity.canonical_name.localeCompare(b.entity.canonical_name)); leads.sort((a, b) => a.display_name.localeCompare(b.display_name)); }
  if (sort === "recent") leads.sort((a, b) => b.last_updated_at.localeCompare(a.last_updated_at));
  return <section className="product-workspace">
    <header className="workspace-heading"><div><h1>Discover</h1><p>Explore emerging companies, founders, and programs.</p></div><button disabled={refresh.isPending} onClick={() => refresh.mutate()}>{refresh.isPending ? "Updating intelligence…" : "Refresh intelligence"}</button></header>
    {refresh.data ? <p role="status">{refresh.data.status === "completed" ? "Intelligence updated." : "Intelligence updated with gaps. Some sources were unavailable."} <Link to="/system/runs">Details</Link></p> : null}{refresh.error ? <ErrorState error={refresh.error} /> : null}
    <div className="workspace-tabs" aria-label="Discover entity type">{["companies", "founders", "programs"].map((value) => <button className={tab === value ? "selected" : "secondary-button"} key={value} onClick={() => setParameters({ type: value })}>{words(value)}</button>)}</div>
    <div className="explore-filters"><label>Search<input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Find an opportunity" /></label><label>Evidence state<select value={state} onChange={(e) => setState(e.target.value)}><option value="all">All evidence states</option><option value="validated">Validated</option><option value="uncertain">Needs corroboration</option></select></label><label>Sort<select value={sort} onChange={(e) => setSort(e.target.value)}><option value="priority">Attention priority</option><option value="name">Name</option><option value="recent">Recently changed</option></select></label></div>
    {tab === "programs" ? <div className="entity-explorer">{programs.programs.filter((p) => p.name.toLowerCase().includes(search.toLowerCase())).map((p) => <article key={p.id}><h2>{p.name}</h2><p>{p.batch}</p><button className="secondary-button" onClick={() => { programs.setSelectedProgram(p.id); setParameters({ type: "companies" }); }}>Explore this program</button></article>)}</div> : <>
      {rankings.isLoading || candidates.loading ? <LoadingState /> : null}{rankings.error || candidates.error ? <ErrorState error={rankings.error ?? candidates.error} /> : null}
      <div className="table-scroll"><table className="opportunity-table"><thead><tr><th>{type === "person" ? "Founder" : "Company"}</th><th>{type === "person" ? "Role / company" : "Founder context"}</th><th>Why surfaced</th><th>Evidence state</th><th>Recent change</th><th>Next action</th></tr></thead><tbody>
        {entities.map((item) => <tr key={item.entity.entity_id}><td><Link to={researchLink(item.entity.entity_id)}>{item.entity.canonical_name}</Link></td><td>{item.entity.current_title ?? "See verified relationships"}</td><td>{entityReason(item)}</td><td>{entityState(item)}<small className="table-detail">Attention score {Math.round(item.ranking.composite_score * 100)}%</small></td><td>{item.movement.rank_delta == null ? "New" : item.movement.rank_delta === 0 ? "Stable" : `${item.movement.rank_delta > 0 ? "+" : ""}${item.movement.rank_delta} places`}</td><td><Link to={researchLink(item.entity.entity_id)}>Research →</Link></td></tr>)}
        {leads.map((item) => <tr key={item.hypothesis_id}><td><Link to={candidateLink(item.hypothesis_id)}>{type === "person" ? item.candidate_name : item.company_name}</Link></td><td>{type === "person" ? item.company_name ?? "Company unresolved" : item.candidate_name ?? "Founder unresolved"}</td><td>{item.why_now?.[0] ?? "Public evidence to investigate"}</td><td>{evidenceState(item)}</td><td>{new Date(item.last_updated_at).toLocaleDateString()}</td><td><Link to={candidateLink(item.hypothesis_id)}>Corroborate →</Link></td></tr>)}
      </tbody></table></div>
      {!rankings.isLoading && !candidates.loading && !entities.length && !leads.length ? <EmptyState title="No matching opportunities yet" detail="Refresh intelligence or change the filters. Evidence must establish an identity before it enters the validated review queue." /> : null}
    </>}
  </section>;
}

export function PortfolioPage() {
  const { selectedProgram } = usePrograms();
  const query = useQuery(decisionsQuery(selectedProgram || undefined));
  const [filter, setFilter] = useState("all");
  const items = Object.values(query.data?.stages ?? {}).flat().filter((i) => filter === "all" || i.crm.stage === filter);
  return <section className="product-workspace"><header className="workspace-heading"><div><h1>Portfolio</h1><p>Tracked opportunities, decisions, and follow-ups.</p></div><Link to="/discover">Find opportunities →</Link></header>
    <label className="portfolio-filter">Status<select value={filter} onChange={(e) => setFilter(e.target.value)}><option value="all">All tracked opportunities</option>{Object.keys(query.data?.stages ?? {}).map((stage) => <option key={stage} value={stage}>{words(stage)}</option>)}</select></label>
    {query.isLoading ? <LoadingState /> : null}{query.error ? <ErrorState error={query.error} /> : null}
    <div className="portfolio-list">{items.map((item) => <article key={item.entity.entity_id}><div><h2><Link to={`${researchLink(item.entity.entity_id)}?view=decisions`}>{item.entity.canonical_name}</Link></h2><p>{item.crm.notes?.at(-1) ?? item.recommendation?.reasoning ?? "Ready for your review"}</p><small>Last reviewed {item.crm.updated_at ? new Date(item.crm.updated_at).toLocaleDateString() : "Not yet"}</small></div><span>{words(item.crm.stage)}</span><div><small>Next action</small><p>{item.recommendation ? words(item.recommendation.action) : "Record a decision or follow-up"}</p><Link to={`${researchLink(item.entity.entity_id)}?view=decisions`}>Update / follow up →</Link></div></article>)}</div>
    {!query.isLoading && !query.error && !items.length ? <EmptyState title="Your worklist is clear" detail="Open a validated company or founder in Research and save a decision to begin tracking it." /> : null}
  </section>;
}
