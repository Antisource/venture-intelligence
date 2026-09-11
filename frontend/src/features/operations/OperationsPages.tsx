import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { apiClient, type EmailSettingsRequest, type EmailSettingsResponse } from "../../api/client.ts";
import { cyclesQuery, emailSettingsQuery, readinessQuery } from "../../api/queries";
import { AsyncState, EmptyState } from "../../components/AsyncState";
import { Icon } from "../../components/Icon";

const alertTypes = [
  "new_high_confidence", "new_founder_discovery", "evidence_changes",
  "contradictions", "stale_knowledge", "program_matches",
  "crm_status_change", "investigation_completed", "research_failures",
] as const;

function label(value: string) { return value.replaceAll("_", " "); }

export function ContextualAnalyst({ contextName }: { contextName?: string }) {
  const [question, setQuestion] = useState("");
  const mutation = useMutation({ mutationFn: () => apiClient.askAnalyst(question.trim().slice(0, 2000), contextName) });
  const submit = (event: FormEvent) => { event.preventDefault(); if (question.trim()) mutation.mutate(); };
  return <section className="page-stack analyst-page">
    <h2>Research assistant</h2><p>{contextName ? <>Scoped to <strong>{contextName}</strong>. Ask about this identity using evidence in this workspace.</> : "Optional local reasoning. Verify its conclusions against the cited evidence."}</p>
    <div className="analyst-workspace">
      <section className="panel analyst-conversation">
        <div className="analyst-message system"><span><Icon name="spark" /></span><div><strong>Research analyst</strong><p>Ask about the validated intelligence already available to this workspace.</p></div></div>
        {mutation.data ? <div className="analyst-message answer"><span><Icon name="spark" /></span><div><strong>Evidence-based response</strong><p className="pre-wrap">{mutation.data.answer}</p><small>Evidence sufficient: {mutation.data.evidence_sufficient ? "yes" : "no"}</small>{mutation.data.citations?.length ? <ul className="plain-list citation-list">{mutation.data.citations.map((citation) => <li key={`${citation.url}-${citation.label}`}><a href={citation.url} target="_blank" rel="noreferrer">{citation.label} ↗</a></li>)}</ul> : null}{mutation.data.caveats?.map((caveat) => <small key={caveat}>{caveat}</small>)}</div></div> : null}
        {mutation.error ? <div className="state-card state-partial"><strong>Assistant unavailable</strong><span>{mutation.error.message}. Public-source research remains available.</span></div> : null}
        <form className="analyst-composer" onSubmit={submit}><textarea aria-label="Analyst question" required maxLength={2000} value={question} onChange={(event) => setQuestion(event.target.value)} placeholder={contextName ? `Ask about ${contextName} using this workspace's evidence` : "What changed, and which evidence should I inspect first?"} /><button type="submit" disabled={mutation.isPending || !question.trim()}><Icon name="arrow" /> {mutation.isPending ? "Reasoning…" : "Ask analyst"}</button></form>
      </section>
    </div>
  </section>;
}

export function SettingsPage() {
  const query = useQuery(emailSettingsQuery());
  return <section className="page-stack settings-page">
    <header className="page-heading compact-heading"><div><p className="eyebrow">WORKSPACE SETTINGS</p><h1>Notifications with explicit boundaries.</h1><p className="lede">Delivery is optional, credentials stay server-side, and email failures never block research.</p></div></header>
    <AsyncState loading={query.isLoading} error={query.error}>
      {query.data ? <SettingsForm initial={query.data} /> : null}
    </AsyncState>
  </section>;
}

function SettingsForm({ initial }: { initial: EmailSettingsResponse }) {
  const client = useQueryClient();
  const [destination, setDestination] = useState(initial.destination ?? "");
  const [enabled, setEnabled] = useState(Boolean(initial.preferences.enabled));
  const [frequency, setFrequency] = useState(String(initial.preferences.frequency ?? "immediate"));
  const [minimumConfidence, setMinimumConfidence] = useState(Number(initial.preferences.minimum_confidence ?? 0.65));
  const [preferences, setPreferences] = useState<Record<string, boolean>>(() => Object.fromEntries(alertTypes.map((item) => [item, initial.preferences[item] !== false])));
  const [status, setStatus] = useState("");
  const save = useMutation({
    mutationFn: (body: EmailSettingsRequest) => apiClient.updateEmailSettings(body),
    onSuccess: async () => { setStatus("Email alert settings saved."); await client.invalidateQueries({ queryKey: ["email-settings"] }); },
  });
  const test = useMutation({ mutationFn: apiClient.testEmail, onSuccess: (result) => setStatus(`Test email: ${result.status}.`) });
  const preview = useMutation({ mutationFn: () => apiClient.emailDigest(false), onSuccess: (result) => setStatus(result.notice ?? "Digest preview generated.") });
  const send = useMutation({ mutationFn: () => apiClient.emailDigest(true), onSuccess: (result) => setStatus(result.notice ?? `Digest: ${result.status}.`) });
  const submit = (event: FormEvent) => { event.preventDefault(); save.mutate({ destination: destination.trim() || null, enabled, frequency, minimum_confidence: minimumConfidence, preferences }); };
  return <form className="settings-layout" onSubmit={submit}>
        <section className="panel settings-main"><div className="section-heading"><div><p className="eyebrow">EMAIL DELIVERY</p><h2>Alert destination</h2></div><label className="switch"><input aria-label="Enable email notifications" type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /><span /></label></div>
          <dl className="compact-facts"><div><dt>SMTP transport</dt><dd>{label(initial.runtime.transport_status)}</dd></div><div><dt>Destination</dt><dd>{label(initial.runtime.destination_status)}</dd></div><div><dt>Sending</dt><dd>{label(initial.runtime.sending_status)}</dd></div><div><dt>Last outcome</dt><dd>{initial.runtime.last_outcome ? label(initial.runtime.last_outcome) : "No send attempted"}</dd></div></dl>
          <label>Recipient email<input aria-label="Destination email" type="email" value={destination} onChange={(event) => setDestination(event.target.value)} placeholder="you@example.com" /></label>
          <div className="form-grid"><label>Frequency<select aria-label="Email alert frequency" value={frequency} onChange={(event) => setFrequency(event.target.value)}><option value="immediate">Immediate</option><option value="daily">Daily digest</option><option value="weekly">Weekly digest</option></select></label><label>Minimum confidence · {Math.round(minimumConfidence * 100)}%<input aria-label="Minimum alert confidence" type="range" min="0" max="1" step="0.05" value={minimumConfidence} onChange={(event) => setMinimumConfidence(Number(event.target.value))} /></label></div>
          <div className="settings-actions"><button type="submit" disabled={save.isPending}><Icon name="check" /> {save.isPending ? "Saving…" : "Save settings"}</button><button className="secondary-button" type="button" disabled={preview.isPending} onClick={() => preview.mutate()}><Icon name="research" /> {preview.isPending ? "Preparing…" : "Preview partner digest"}</button><button className="secondary-button" type="button" disabled={send.isPending} onClick={() => send.mutate()}><Icon name="arrow" /> {send.isPending ? "Sending…" : "Send partner digest"}</button><button className="secondary-button" type="button" disabled={test.isPending} onClick={() => test.mutate()}><Icon name="alert" /> {test.isPending ? "Sending…" : "Send test email"}</button></div>
          {preview.data?.body ? <pre className="digest-preview">{preview.data.subject}{"\n\n"}{preview.data.body}</pre> : null}
          {status ? <p className="success-note" role="status">{status}</p> : null}{save.error || test.error || preview.error || send.error ? <p className="form-error" role="alert">{(save.error ?? test.error ?? preview.error ?? send.error)?.message}</p> : null}
        </section>
        <aside className="panel settings-types"><p className="eyebrow">ALERT TYPES</p><h2>What reaches your inbox</h2><div className="preference-list">{alertTypes.map((item) => <label key={item}><span><strong>{label(item)}</strong><small>Threshold and evidence policy still apply</small></span><input type="checkbox" checked={preferences[item]} onChange={(event) => setPreferences((current) => ({ ...current, [item]: event.target.checked }))} /></label>)}</div></aside>
      </form>;
}

export function RunsPage() {
  const readiness = useQuery(readinessQuery());
  const cycles = useQuery(cyclesQuery());
  const queryClient = useQueryClient();
  const scheduler = useMutation({
    mutationFn: (enabled: boolean) => apiClient.configureScheduler(enabled),
    onSuccess: async () => queryClient.invalidateQueries({
      queryKey: ["intelligence-cycles"],
    }),
  });
  const schedulerState = cycles.data?.scheduler;
  const cycleRecords = cycles.data?.cycles ?? [];
  return <section className="page-stack operations-page">
    <header className="page-heading compact-heading"><div><p className="eyebrow">SYSTEM READINESS</p><h1>Know what works before the next cycle.</h1><p className="lede">Product-level health, persisted intelligence runs, and truthful recovery actions—without exposing credentials.</p></div></header>
    <AsyncState loading={readiness.isLoading || cycles.isLoading} error={readiness.error ?? cycles.error}>
      <div className="readiness-grid">
        {readiness.data?.components.map((component) => <article className="panel readiness-card" key={component.component}><header><strong>{component.component}</strong><span className={"status-pill " + (component.status === "READY" ? "enabled" : "optional")}>{component.status.replaceAll("_", " ")}</span></header><p>{component.summary}</p>{component.action ? <small>{component.action}</small> : null}{component.technical_status ? <details><summary>Technical detail</summary><code>{component.technical_status}</code></details> : null}</article>)}
      </div>
      <section className="panel">
        <div className="section-heading"><div><p className="eyebrow">12-HOUR CYCLE</p><h2>Scheduler state</h2></div><button className="secondary-button" type="button" disabled={scheduler.isPending} onClick={() => scheduler.mutate(!schedulerState?.enabled)}>{schedulerState?.enabled ? "Disable scheduler" : "Enable scheduler"}</button></div>
        <dl className="compact-facts"><div><dt>Status</dt><dd>{schedulerState?.running ? "Running in foreground" : schedulerState?.enabled ? "Enabled; start via CLI" : "Disabled"}</dd></div><div><dt>Interval</dt><dd>{schedulerState?.interval_hours ?? 12} hours</dd></div><div><dt>Last run</dt><dd>{schedulerState?.last_run_at ? new Date(schedulerState.last_run_at).toLocaleString() : "Never"}</dd></div><div><dt>Next run</dt><dd>{schedulerState?.next_run_at ? new Date(schedulerState.next_run_at).toLocaleString() : "Start scheduler to schedule"}</dd></div></dl>
      </section>
      <section className="panel">
        <div className="section-heading"><div><p className="eyebrow">RUN HISTORY</p><h2>Inspectable collection cycles</h2></div><span>{cycleRecords.length} retained</span></div>
        {!cycleRecords.length ? <EmptyState title="No intelligence cycles yet" detail="Run a cycle to capture provider eligibility, requests, observations, hypotheses, changes, and stop reasons." /> : <div className="run-history-list">{cycleRecords.map((cycle) => <article key={cycle.cycle_id}><header><strong>{cycle.status.replaceAll("_", " ")}</strong><time>{cycle.started_at ? new Date(cycle.started_at).toLocaleString() : "Unknown start time"}</time></header><p>{(cycle.program_results ?? []).map((result) => result.program_id + " · " + result.batch_id + ": " + (result.hypotheses ?? 0) + " hypotheses, " + (result.promotions ?? 0) + " promotions, " + (result.changes?.length ?? 0) + " changes").join(" · ") || cycle.error || "No active programs selected."}</p><details><summary>Run diagnostics</summary><pre>{JSON.stringify(cycle, null, 2)}</pre></details></article>)}</div>}
      </section>
    </AsyncState>
  </section>;
}
