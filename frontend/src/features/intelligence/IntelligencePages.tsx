import { useMemo, useState, type ReactNode } from "react";

import type {
  EntityIntelligenceResponse,
} from "../../api/client";
import { EmptyState } from "../../components/AsyncState";
import { Icon } from "../../components/Icon";


function label(value: string): string {
  return value.replaceAll("_", " ");
}

function percentage(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function Movement({ delta }: { delta: number | null | undefined }) {
  if (delta === null || delta === undefined) return <span className="movement neutral">New</span>;
  if (delta === 0) return <span className="movement neutral">—</span>;
  return <span className={`movement ${delta > 0 ? "up" : "down"}`}>{delta > 0 ? "↑" : "↓"} {Math.abs(delta)}</span>;
}

export function IntelligenceLineage({ data }: { data: EntityIntelligenceResponse }) {
  const [selectedSignalId, setSelectedSignalId] = useState(data.signals[0]?.signal_id ?? "");
  const selectedSignal = data.signals.find((item) => item.signal_id === selectedSignalId);
  const selectedFeatures = useMemo(
    () => data.features.filter((item) => (
      selectedSignal?.feature_ids ?? []
    ).includes(item.feature_id ?? "")),
    [data.features, selectedSignal],
  );
  const evidenceIds = useMemo(
    () => new Set(selectedFeatures.flatMap((item) => item.evidence_ids ?? [])),
    [selectedFeatures],
  );
  const selectedEvidence = data.evidence.filter((item) => evidenceIds.has(item.evidence_id));
  const attachedEvidenceCount = data.evidence.filter((item) => item.attribution_status === "attached").length;
  const whyHere = data.entity.program_status === "verified" && data.entity.batch_id
    ? `Verified ${data.entity.batch_id} participant with evidence-backed identity.`
    : data.entity.independent_evidence_origins
      ? `${data.entity.independent_evidence_origins} independent origins support this identity.`
      : "This identity is retained for analyst review with its uncertainty visible.";
  return (
    <section className="page-stack">
      <section className="entity-story-grid">
        <article className="panel entity-story"><p className="eyebrow">INTELLIGENCE SUMMARY</p><h2>Why this identity is here</h2><p>{whyHere}</p><dl className="compact-facts"><div><dt>Identity</dt><dd>{label(data.entity.identity_status)}</dd></div><div><dt>Program</dt><dd>{label(data.entity.program_status)}{data.entity.program_id ? ` · ${data.entity.program_id}` : ""}</dd></div><div><dt>Batch</dt><dd>{data.entity.batch_id ?? "Unknown"}</dd></div><div><dt>Attention score</dt><dd>{data.ranking ? percentage(data.ranking.composite_score) : "Not ranked"}</dd></div></dl></article>
        <article className="panel entity-story"><p className="eyebrow">WHAT CHANGED</p><h2>{data.movement.rank_delta == null ? "New to the review queue" : data.movement.rank_delta > 0 ? `Moved up ${data.movement.rank_delta} places` : data.movement.rank_delta < 0 ? `Moved down ${Math.abs(data.movement.rank_delta)} places` : "Position is stable"}</h2><p>{data.movement.score_delta == null ? "No prior comparable score is available." : `Attention score changed by ${data.movement.score_delta > 0 ? "+" : ""}${data.movement.score_delta.toFixed(3)}.`}</p><span className="evidence-count"><Icon name="check" /> {attachedEvidenceCount} attached evidence records · {data.entity.independent_evidence_origins} independent origins</span></article>
      </section>
      <section className="panel lineage-workbench">
        <div className="card-title-row"><div><p className="eyebrow">EXPLAINABLE LINEAGE</p><h2>Evidence → feature → signal → ranking</h2></div><span className="status-pill">{data.lineage.length} links</span></div>
        {data.signals.length ? (
          <div className="signal-tabs" role="tablist" aria-label="Signals">
            {data.signals.map((signal) => {
              const signalId = signal.signal_id ?? signal.input_digest ?? signal.signal_type;
              return <button type="button" role="tab" aria-selected={signalId === selectedSignalId} className={signalId === selectedSignalId ? "selected" : ""} key={signalId} onClick={() => setSelectedSignalId(signalId)}><span>{label(signal.signal_type)}</span><strong>{percentage(signal.value)}</strong></button>;
            })}
          </div>
        ) : <EmptyState title="No normalized signals" detail="This identity has not accumulated enough feature evidence for a signal." />}
        {selectedSignal ? <div className="lineage-columns">
          <LineageColumn title="Evidence" count={selectedEvidence.length}>{selectedEvidence.map((item) => <article key={item.evidence_id}><strong>{item.claim}</strong><span>{percentage(item.confidence)} confidence</span><small>{(item.sources ?? []).join(", ") || "Unknown source"}</small>{(item.source_urls ?? []).map((url) => <a key={url} href={url} target="_blank" rel="noreferrer">Open source ↗</a>)}</article>)}</LineageColumn>
          <LineageColumn title="Features" count={selectedFeatures.length}>{selectedFeatures.map((item) => <article key={item.feature_id ?? item.input_digest}><strong>{label(item.name)} = {item.value}</strong><span>{item.formula}</span><small>{item.feature_version} · as of {item.as_of ? new Date(item.as_of).toLocaleString() : "unknown"}</small></article>)}</LineageColumn>
          <LineageColumn title="Selected signal" count={1}><article><strong>{label(selectedSignal.signal_type)} · {percentage(selectedSignal.value)}</strong><span>{selectedSignal.explanation}</span><small>{selectedSignal.signal_version} · {percentage(selectedSignal.confidence)} confidence</small></article></LineageColumn>
        </div> : null}
      </section>
      <section className="panel ranking-history"><div className="card-title-row"><div><p className="eyebrow">RANKING HISTORY</p><h2>Historically reproducible score snapshots</h2></div><Movement delta={data.movement.rank_delta} /></div>{data.ranking_history?.length ? <div className="table-scroll"><table className="evaluation-table"><thead><tr><th>As of</th><th>Rank</th><th>Score</th><th>Version</th><th>Reason</th></tr></thead><tbody>{data.ranking_history.map((item) => <tr key={item.ranking_id ?? item.input_digest}><td>{item.ranked_at ? new Date(item.ranked_at).toLocaleString() : "unknown"}</td><td>#{item.rank}</td><td>{item.composite_score.toFixed(3)}</td><td>{item.ranking_version}</td><td>{item.reasoning}</td></tr>)}</tbody></table></div> : <EmptyState title="No ranking history" />}</section>
    </section>
  );
}

function LineageColumn({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  return <section className="lineage-column"><header><h3>{title}</h3><span>{count}</span></header><div>{children}</div></section>;
}
