import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import type { EvaluationRun } from "../../api/client";
import {
  evaluationContractQuery,
  evaluationQuery,
  evaluationsQuery,
} from "../../api/queries";
import { AsyncState, EmptyState, PartialState } from "../../components/AsyncState";
import { Icon } from "../../components/Icon";

function metricValue(metric: { unit: string; value: number }): string {
  if (metric.unit === "ratio") return `${(metric.value * 100).toFixed(1)}%`;
  if (metric.unit === "days") return `${metric.value.toFixed(1)} days`;
  return String(metric.value);
}

function label(value: string): string {
  return value.replaceAll("_", " ");
}

export function EvaluationPage() {
  const contract = useQuery(evaluationContractQuery());
  const runs = useQuery(evaluationsQuery());
  const [selectedRun, setSelectedRun] = useState("");
  const effectiveRun = selectedRun || runs.data?.evaluations[0]?.evaluation_run_id || "";
  const detail = useQuery(evaluationQuery(effectiveRun));

  return (
    <section className="page-stack">
      <header className="page-heading">
        <p className="eyebrow">HISTORICAL DIAGNOSTICS</p>
        <h1>Understand what the system knew—and what it missed.</h1>
        <p className="lede">
          Reports are reproduced from isolated replay snapshots. Evaluation cannot
          collect sources, send alerts, or mutate live CRM and ranking state.
        </p>
      </header>
      <AsyncState loading={contract.isLoading} error={contract.error}>
        <div className="evaluation-contract-strip">
          <span><Icon name="history" /><span><small>Historical boundary</small><strong>{contract.data?.historical_cutoff_field}</strong></span></span>
          <span><Icon name="check" /><span><small>Execution</small><strong>{contract.data?.remote_execution_enabled ? "Remote" : "Local only"}</strong></span></span>
          <span><Icon name="evaluate" /><span><small>Source ablation</small><strong>{contract.data?.source_ablation_enabled ? "Available" : "Unavailable"}</strong></span></span>
        </div>
      </AsyncState>

      <AsyncState
        loading={runs.isLoading}
        error={runs.error}
        empty={runs.data?.evaluations.length === 0}
      >
        {runs.data?.evaluations.length ? (
          <div className="evaluation-layout">
            <aside className="panel evaluation-runs" aria-label="Evaluation runs">
              <p className="eyebrow">REPORT SNAPSHOTS</p>
              {runs.data.evaluations.map((run) => (
                <button
                  type="button"
                  key={run.evaluation_run_id}
                  className={run.evaluation_run_id === effectiveRun ? "selected" : ""}
                  onClick={() => setSelectedRun(run.evaluation_run_id)}
                >
                  <strong>{run.program_id} · {run.batch_id}</strong>
                  <span>{new Date(run.cutoff).toLocaleString()}</span>
                  <small>{label(run.status)} · {label(run.target_level)}</small>
                </button>
              ))}
            </aside>
            <AsyncState loading={detail.isLoading} error={detail.error}>
              {detail.data ? <EvaluationReport run={detail.data} /> : null}
            </AsyncState>
          </div>
        ) : null}
      </AsyncState>
    </section>
  );
}

function EvaluationReport({ run }: { run: EvaluationRun }) {
  return (
    <article className="evaluation-report">
      {run.status === "partial" ? (
        <PartialState>{(run.warnings ?? []).join(", ")}</PartialState>
      ) : null}
      <section className="metric-grid" aria-label="Evaluation metrics">
        {run.metrics.map((metric) => (
          <div className="metric-card" key={metric.name}>
            <span>{label(metric.name)}</span>
            <strong>{metricValue(metric)}</strong>
            <small>n={metric.sample_size} · {metric.definition}</small>
          </div>
        ))}
      </section>
      <DiagnosticTable
        eyebrow="MISSED ENTITIES"
        title="Where the historical pipeline lost positive outcomes"
        count={run.missed_entities.length}
      >
        {run.missed_entities.length ? (
          <div className="table-scroll"><table className="evaluation-table">
            <thead><tr><th>Identity</th><th>Type</th><th>Failure category</th><th>Evidence</th><th>Signals</th></tr></thead>
            <tbody>{run.missed_entities.map((item) => (
              <tr key={`${item.entity_type}-${item.canonical_name}`}>
                <td>{item.canonical_name}</td><td>{item.entity_type}</td><td>{label(item.category)}</td>
                <td>{item.evidence_count}</td><td>{item.signal_count}</td>
              </tr>
            ))}</tbody>
          </table></div>
        ) : <EmptyState title="No missed positive identities" />}
      </DiagnosticTable>
      <DiagnosticTable eyebrow="FALSE POSITIVES" title="Top-25 ranking diagnostics" count={run.false_positives.length} warning>
        {run.false_positives.length ? (
          <div className="table-scroll"><table className="evaluation-table">
            <thead><tr><th>Rank</th><th>Identity</th><th>Score</th><th>Confidence</th><th>Signals</th></tr></thead>
            <tbody>{run.false_positives.map((item) => (
              <tr key={item.entity_id}><td>{item.rank}</td><td>{item.canonical_name}</td>
                <td>{item.composite_score.toFixed(3)}</td><td>{(item.confidence * 100).toFixed(1)}%</td>
                <td>{(item.signals ?? []).join(", ") || "No replay signals"}</td></tr>
            ))}</tbody>
          </table></div>
        ) : <EmptyState title="No false positives in the top 25" />}
      </DiagnosticTable>
      <section className="panel report-provenance">
        <p className="eyebrow">REPRODUCIBILITY</p>
        <dl>
          <div><dt>Report digest</dt><dd>{run.report_digest}</dd></div>
          <div><dt>Replay digest</dt><dd>{run.snapshot.replay_projection_digest}</dd></div>
          <div><dt>Ground truth</dt><dd>{run.ground_truth_digest}</dd></div>
        </dl>
      </section>
    </article>
  );
}

function DiagnosticTable({
  eyebrow,
  title,
  count,
  warning,
  children,
}: {
  eyebrow: string;
  title: string;
  count: number;
  warning?: boolean;
  children: ReactNode;
}) {
  return (
    <section className="panel evaluation-table-panel">
      <div className="card-title-row">
        <div><p className="eyebrow">{eyebrow}</p><h2>{title}</h2></div>
        <span className={`status-pill ${warning ? "optional" : ""}`}>{count}</span>
      </div>
      {children}
    </section>
  );
}
