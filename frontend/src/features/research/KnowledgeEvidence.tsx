import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../../api/client.ts";
import { EmptyState, ErrorState, LoadingState } from "../../components/AsyncState";

export function KnowledgeEvidence({ entityId, entityType }: { entityId: string; entityType: string }) {
  const client = useQueryClient();
  const [connection, setConnection] = useState("Polling every 15 seconds");
  const query = useQuery({ queryKey: ["knowledge", entityId, entityType], queryFn: () => apiClient.knowledge(entityType, entityId), refetchInterval: 15000 });
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const stream = new EventSource(`/api/v1/knowledge/stream/${encodeURIComponent(entityId)}`);
    stream.onopen = () => setConnection("Live updates connected");
    const refresh = () => {
      for (const key of ["knowledge", "entity-intelligence", "ranking-intelligence", "decisions", "decision", "temporal-graph"]) void client.invalidateQueries({ queryKey: [key] });
    };
    stream.addEventListener("knowledge.updated", refresh);
    const fallback = () => { stream.close(); setConnection("Polling every 15 seconds"); };
    stream.onerror = fallback;
    stream.addEventListener("knowledge.unavailable", fallback);
    return () => stream.close();
  }, [client, entityId]);
  return <section className="knowledge-evidence"><h2>Knowledge / categorized evidence</h2><p role="status">{connection}</p>
    {query.isLoading ? <LoadingState /> : null}{query.error ? <ErrorState error={query.error} /> : null}
    {query.data ? <>
      <p>{query.data.overall_confidence_pct}% overall confidence · Updated {query.data.updated_at ? new Date(query.data.updated_at).toLocaleString() : "Unknown"}</p>
      {Object.entries(query.data.evidence_by_category ?? {}).map(([category, items]) => <details key={category}><summary>{category.replaceAll("_", " ")} · {String(query.data?.category_confidence?.[category]?.confidence_pct ?? "Unscored")}% · {items.length} evidence</summary>{items.map((item, index) => <article key={index}><h3>{String(item.claim ?? item.title ?? "Public evidence")}</h3><p>{String(item.confidence_pct ?? "Unscored")}% · {String(item.temporal_status ?? "unknown")}</p>{typeof item.canonical_url === "string" && /^https?:\/\//i.test(item.canonical_url) ? <a href={item.canonical_url} target="_blank" rel="noreferrer">Source ↗</a> : null}<details><summary>Provenance</summary><pre>{JSON.stringify(item, null, 2)}</pre></details></article>)}</details>)}
      {query.data.claims?.map((claim) => <article key={claim.claim_id}><p>{claim.statement}</p><small>{claim.status} · {Math.round(claim.confidence * 100)}%</small></article>)}
    </> : !query.isLoading && !query.error ? <EmptyState title="No compiled knowledge page yet" detail="Canonical evidence below remains available; no wiki facts are invented." /> : null}
  </section>;
}
