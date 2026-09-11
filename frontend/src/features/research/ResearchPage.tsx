import { useMutation } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";

import { apiClient, type ResearchResponse } from "../../api/client.ts";
import { EmptyState, ErrorState, LoadingState } from "../../components/AsyncState";
import { Icon } from "../../components/Icon";
import { usePrograms } from "../../state/ProgramProvider";

const RECENT_KEY = "venture:recent-research";

interface RecentResearch {
  name: string;
  entityType: string;
  programId: string;
}

function confidence(value?: number | null, fallback?: string) {
  return value == null ? fallback ?? "Unscored" : `${Math.round(value * 100)}%`;
}

function sourceLabel(sourceDomain: string | null | undefined, url: string) {
  if (sourceDomain) return sourceDomain;
  try {
    return new URL(url).hostname;
  } catch {
    return "Unknown source";
  }
}

function readRecent(): RecentResearch[] {
  try {
    const value = JSON.parse(window.localStorage.getItem(RECENT_KEY) ?? "[]") as unknown;
    return Array.isArray(value) ? value.filter((item): item is RecentResearch => item && typeof item.name === "string" && typeof item.entityType === "string" && typeof item.programId === "string").slice(0, 5).map(({ name, entityType, programId }) => ({ name, entityType, programId })) : [];
  } catch {
    return [];
  }
}

export function ResearchCollector({ initialName = "", initialType = "company" }: { initialName?: string; initialType?: string }) {
  const programs = usePrograms();
  const [name, setName] = useState(initialName);
  const [entityType, setEntityType] = useState(initialType);
  const [recent, setRecent] = useState<RecentResearch[]>(readRecent);
  const mutation = useMutation({
    mutationFn: () => apiClient.research({
      entity_name: name.trim(),
      entity_type: entityType,
      program_id: programs.selectedProgram || null,
      budget: { max_queries: 8, max_depth: 2, max_results_per_query: 5, max_runtime_seconds: 20 },
    }),
    onSuccess: () => {
      const next = [{ name: name.trim(), entityType, programId: programs.selectedProgram }, ...recent]
        .filter((item, index, items) => items.findIndex((candidate) => candidate.name === item.name && candidate.entityType === item.entityType) === index)
        .slice(0, 5);
      setRecent(next);
      window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
    },
  });
  const evidenceByAttribution = useMemo(() => {
    const grouped = new Map<string, ResearchResponse["evidence"]>();
    for (const item of mutation.data?.evidence ?? []) {
      grouped.set(item.attribution_status, [...(grouped.get(item.attribution_status) ?? []), item]);
    }
    return [...grouped.entries()];
  }, [mutation.data]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (name.trim()) mutation.mutate();
  };

  return (
    <section className="page-stack research-page">
      <div className="research-workbench consolidated-research">
        <aside className="research-context panel">
          <div><p className="eyebrow">INVESTIGATION</p><h2>Research target</h2></div>
          <form onSubmit={submit} className="research-native-form">
            <label>Company, founder, product, or program<input aria-label="Research target" required maxLength={200} value={name} onChange={(event) => setName(event.target.value)} placeholder="Who or what are you investigating?" /></label>
            <label>Target type<select aria-label="Target type" value={entityType} onChange={(event) => setEntityType(event.target.value)}><option value="company">Company</option><option value="person">Person</option><option value="product">Product</option><option value="program">Program</option><option value="other">Other</option></select></label>
            <div className="budget-brief"><span><Icon name="check" /> 8 queries maximum</span><span><Icon name="check" /> 20 second bound</span><span><Icon name="check" /> Public sources only</span></div>
            <button type="submit" disabled={mutation.isPending || !name.trim()}><Icon name="research" /> {mutation.isPending ? "Researching…" : "Investigate target"}</button>
          </form>
          <div className="recent-research">
            <p className="summary-label">RECENT CONTEXT</p>
            {recent.map((item) => <button type="button" key={`${item.entityType}-${item.name}`} onClick={() => { setName(item.name); setEntityType(item.entityType); programs.setSelectedProgram(item.programId); }}><span>{item.name}</span><small>{item.entityType} · Saved target</small></button>)}
            {!recent.length ? <p>No saved investigations on this device.</p> : null}
          </div>
        </aside>
        <main className="research-results panel" aria-live="polite">
          {!mutation.data && !mutation.isPending && !mutation.error ? <div className="research-welcome"><span className="research-orbit"><Icon name="spark" /></span><h2>Begin with a research question</h2><p>The result will stay in this workspace with categorized evidence, confidence, provenance, and collection stop reasons.</p></div> : null}
          {mutation.isPending ? <LoadingState label="Searching permitted public sources within the configured budget…" /> : null}
          {mutation.error ? <ErrorState error={mutation.error} /> : null}
          {mutation.data ? <>
            <header className="result-header"><div><p className="eyebrow">RESEARCH RESULT</p><h2>{mutation.data.target.name}</h2><p>{mutation.data.attached_evidence_count} attached · {mutation.data.unbound_evidence_count} unbound · {mutation.data.contradictory_evidence_count} contradictory</p></div><div className="confidence-ring"><strong>{mutation.data.identity_status.replaceAll("_", " ")}</strong><span>Identity status</span></div></header>
            <div className="research-status-row"><span>Program {mutation.data.program_status.replaceAll("_", " ")}</span><span>{mutation.data.independent_evidence_origins} independent attached origins</span><span>Evidence strength {confidence(mutation.data.confidence, mutation.data.confidence_label)}</span></div>
            <div className="research-status-row"><span className="status-pill">{mutation.data.stop_reason.replaceAll("_", " ")}</span><span>Candidate promotion remains governed by the backend identity gate.</span></div>
            {evidenceByAttribution.length ? <div className="evidence-category-list">{evidenceByAttribution.map(([status, items]) => <section key={status}><header><div><h3>{status === "attached" ? "Canonical / attached evidence" : status === "contradictory" ? "Contradictory evidence" : "Ambiguous / unbound leads"}</h3><small>{status === "attached" ? "May support the canonical identity." : status === "contradictory" ? "Reduces or blocks the claim." : "Retained for review; excluded from identity confidence."}</small></div><span>{items.length}</span></header>{items.map((item) => <article key={item.evidence_origin_url ?? item.url}><div><strong>{item.claim ?? item.title ?? "Public-source evidence"}</strong><span>{status === "attached" ? `${confidence(item.confidence, item.confidence_label ?? undefined)} evidence strength` : status}</span></div><p>{item.category?.replaceAll("_", " ") ?? "Public evidence"} · {sourceLabel(item.source_domain, item.url)}</p>{item.acquisition_providers.length ? <small>Acquired via {item.acquisition_providers.join(", ")}</small> : null}<a href={item.url} target="_blank" rel="noreferrer">Inspect source <Icon name="arrow" /></a></article>)}</section>)}</div> : <EmptyState title="No evidence collected" detail="The bounded run completed without promotable public evidence. No entity was manufactured." />}
            {mutation.data.failures?.length ? <details className="structured-failures"><summary>{mutation.data.failures.length} structured source failures</summary>{mutation.data.failures.map((failure, index) => <p key={`${failure.source}-${index}`}>{failure.source ?? "source"} · {failure.status ?? "failed"} · {failure.error ?? "No detail"}</p>)}</details> : null}
          </> : null}
        </main>
      </div>
    </section>
  );
}
