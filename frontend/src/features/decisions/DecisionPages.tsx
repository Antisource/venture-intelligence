import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import {
  recordCrmAction,
  updateAlertLifecycle,
  updateRecommendationLifecycle,
  type CRMActionRequest,
  type CRMActionType,
  type CRMStage,
  type DecisionEntityResponse,
} from "../../api/client.ts";
import { EmptyState } from "../../components/AsyncState";

const actions: CRMActionType[] = [
  "meeting_scheduled",
  "interested",
  "introduced",
  "watchlisted",
  "follow_up_requested",
  "relationship_started",
  "passed",
  "invested",
  "note_added",
  "stage_changed",
];

const passReasons = [
  ["investment_decision", "Investment decision — not a factual error"],
  ["wrong_identity", "Wrong person or company"],
  ["duplicate", "Duplicate"],
  ["stale_information", "Stale information"],
  ["not_a_founder", "Not actually a founder"],
  ["weak_evidence", "Weak evidence"],
  ["irrelevant_company", "Irrelevant company"],
  ["incorrect_program_inference", "Incorrect program inference"],
  ["already_known", "Already known"],
  ["not_venture_relevant", "Not venture relevant"],
  ["other", "Other"],
] as const;

const factualFalsePositiveReasons = new Set([
  "wrong_identity",
  "duplicate",
  "stale_information",
  "not_a_founder",
  "weak_evidence",
  "incorrect_program_inference",
]);

function label(value: string): string {
  return value.replaceAll("_", " ");
}

function date(value?: string): string {
  return value ? new Date(value).toLocaleString() : "Unknown time";
}

export function DecisionDetails({ data }: { data: DecisionEntityResponse }) {
  return <section className="page-stack"><ActionComposer entityId={data.intelligence.entity.entity_id} data={data} /><ActionHistory data={data} /><RecommendationHistory data={data} /><AlertHistory data={data} /><details><summary>Decision score lineage</summary><FeedbackLineage data={data} /></details></section>;
}

function ActionComposer({ entityId, data }: { entityId: string; data: DecisionEntityResponse }) {
  const client = useQueryClient();
  const [action, setAction] = useState<CRMActionType>("interested");
  const [partner, setPartner] = useState("");
  const [note, setNote] = useState("");
  const [toStage, setToStage] = useState<CRMStage | "">("");
  const [passReason, setPassReason] = useState("investment_decision");
  const mutation = useMutation({
    mutationFn: (request: CRMActionRequest) => recordCrmAction(entityId, request),
    onSuccess: async () => {
      setNote("");
      await Promise.all([
        client.invalidateQueries({ queryKey: ["decisions"] }),
        client.invalidateQueries({ queryKey: ["decision", entityId] }),
        client.invalidateQueries({ queryKey: ["entity-intelligence", entityId] }),
        client.invalidateQueries({ queryKey: ["ranking-intelligence"] }),
      ]);
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    mutation.mutate({
      action,
      partner: partner.trim(),
      note: note.trim() || null,
      to_stage: action === "stage_changed" ? toStage || null : null,
      provenance: {
        channel: "react_decision_workspace",
        authoritative_ground_truth: false,
        ...(action === "passed" ? {
          review_reason: passReason,
          factual_false_positive: factualFalsePositiveReasons.has(passReason),
        } : {}),
      },
    });
  };
  return (
    <form className="panel action-composer" onSubmit={submit}>
      <div><p className="eyebrow">DECISIONS</p><h2>Save a decision or follow-up</h2></div>
      <label>Action<select value={action} onChange={(event) => setAction(event.target.value as CRMActionType)}>{actions.map((item) => <option value={item} key={item}>{label(item)}</option>)}</select></label>
      <label>Actor<input required value={partner} onChange={(event) => setPartner(event.target.value)} placeholder="Partner or reviewer" /></label>
      {action === "passed" ? <label>Pass reason<select aria-label="Pass reason" value={passReason} onChange={(event) => setPassReason(event.target.value)}>{passReasons.map(([value, text]) => <option value={value} key={value}>{text}</option>)}</select></label> : null}
      {action === "stage_changed" ? <label>Next stage<select required value={toStage} onChange={(event) => setToStage(event.target.value as CRMStage)}><option value="">Select allowed stage</option>{data.allowed_transitions.map((item) => <option value={item} key={item}>{label(item)}</option>)}</select></label> : null}
      <label className="action-note">Note<textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="Optional evidence-aware context" /></label>
      <button type="submit" disabled={mutation.isPending || !partner.trim()}>{mutation.isPending ? "Recording…" : "Save decision"}</button>
      {mutation.error ? <p className="form-error" role="alert">{mutation.error.message}</p> : null}
    </form>
  );
}

function FeedbackLineage({ data }: { data: DecisionEntityResponse }) {
  return (
    <section className="panel decision-section">
      <div className="card-title-row"><div><p className="eyebrow">FEEDBACK FEATURES</p><h2>Bounded score influence</h2></div><span className="status-pill">not ground truth</span></div>
      {data.feedback_features.length ? data.feedback_features.map((feature) => (
        <article className="feedback-feature" key={feature.feature_id ?? feature.input_digest}>
          <div><strong>{label(feature.name)}</strong><span>{String(feature.value)} {feature.unit ?? ""}</span></div>
          <p>{feature.formula}</p>
          <small>{feature.feature_version} · {feature.crm_action_ids?.length ?? 0} linked action(s) · {Math.round(feature.confidence * 100)}% confidence</small>
        </article>
      )) : <EmptyState title="No feedback features yet" detail="Partner actions have not yet produced a bounded feedback signal." />}
    </section>
  );
}

function ActionHistory({ data }: { data: DecisionEntityResponse }) {
  return (
    <section className="panel decision-section">
      <div className="card-title-row"><div><p className="eyebrow">APPEND-ONLY AUDIT</p><h2>CRM action history</h2></div><span className="status-pill">{data.actions.length}</span></div>
      {data.actions.length ? <ol className="decision-timeline">{data.actions.map((item) => (
        <li key={item.crm_event_id ?? item.input_digest}>
          <div><strong>{label(item.action)}</strong><span className="status-pill optional">{label(item.feedback_kind)}</span></div>
          <p>{item.note || `${label(item.from_stage)} → ${label(item.to_stage)}`}</p>
          <small>{item.partner} · occurred {date(item.occurred_at)} · observed {date(item.observed_at)}</small>
        </li>
      ))}</ol> : <EmptyState title="No partner actions" />}
    </section>
  );
}

function RecommendationHistory({ data }: { data: DecisionEntityResponse }) {
  const client = useQueryClient();
  const entityId = data.intelligence.entity.entity_id;
  const mutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: "accepted" | "dismissed" | "completed" }) => updateRecommendationLifecycle(id, { status, actor: "react_user" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["decision", entityId] }),
  });
  const [current, ...history] = data.recommendations;
  return (
    <section className="panel decision-section">
      <div className="card-title-row"><div><p className="eyebrow">RECOMMENDATIONS</p><h2>Current decision guidance</h2></div><span className="status-pill">{current ? "Current" : "None"}</span></div>
      {current ? <article className="lifecycle-card" key={current.recommendation.recommendation_id}>
        <div><strong>{label(current.recommendation.action)}</strong><span>{Math.round(current.recommendation.confidence * 100)}% recommendation support</span></div>
        <p>{current.recommendation.reasoning}</p>
        <small>{current.recommendation.recommendation_version} · {current.recommendation.crm_action_ids?.length ?? 0} CRM actions · status {current.lifecycle?.at(-1)?.status ?? "created"}</small>
        <div className="lifecycle-actions">{(["accepted", "dismissed", "completed"] as const).map((status) => <button type="button" key={status} disabled={mutation.isPending} onClick={() => mutation.mutate({ id: current.recommendation.recommendation_id ?? "", status })}>{label(status)}</button>)}</div>
      </article> : <EmptyState title="No recommendation" detail="This entity has not crossed the evidence and ranking thresholds." />}
      {history.length ? <details><summary>Previous versions ({history.length})</summary><div className="decision-timeline">{history.map(({ recommendation, lifecycle }) => <article className="lifecycle-card" key={recommendation.recommendation_id}><div><strong>{label(recommendation.action)}</strong><span>{Math.round(recommendation.confidence * 100)}% recommendation support</span></div><p>{recommendation.reasoning}</p><small>{recommendation.recommendation_version} · status {lifecycle?.at(-1)?.status ?? "superseded"}</small></article>)}</div></details> : null}
    </section>
  );
}

function AlertHistory({ data }: { data: DecisionEntityResponse }) {
  const client = useQueryClient();
  const entityId = data.intelligence.entity.entity_id;
  const mutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: "acknowledged" | "dismissed" | "resolved" }) => updateAlertLifecycle(id, { status, actor: "react_user" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["decision", entityId] }),
  });
  return (
    <section className="panel decision-section">
      <div className="card-title-row"><div><p className="eyebrow">ALERTS</p><h2>Delivery and review lifecycle</h2></div><span className="status-pill">{data.alerts.length}</span></div>
      {data.alerts.length ? data.alerts.map(({ alert, lifecycle }) => (
        <article className="lifecycle-card" key={alert.alert_id}>
          <div><strong>{alert.entity_name} · {label(alert.action)}</strong><span>{lifecycle?.at(-1)?.status ?? "created"}</span></div>
          <p>{alert.reasoning}</p>
          <small>{alert.alert_version} · fired {date(alert.fired_at)}</small>
          <div className="lifecycle-actions">{(["acknowledged", "dismissed", "resolved"] as const).map((status) => <button type="button" key={status} disabled={mutation.isPending} onClick={() => mutation.mutate({ id: alert.alert_id ?? "", status })}>{label(status)}</button>)}</div>
        </article>
      )) : <EmptyState title="No alerts" detail="No recommendation has cleared the configured alert thresholds." />}
    </section>
  );
}
