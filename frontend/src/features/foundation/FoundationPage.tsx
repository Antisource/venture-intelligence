import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "../../api/client.ts";
import {
  sourcePoliciesQuery,
  sourceQualityContractQuery,
  sourceQualityQuery,
} from "../../api/queries";
import { AsyncState, EmptyState, PartialState } from "../../components/AsyncState";

export function SourcePolicyPage() {
  const queryClient = useQueryClient();
  const query = useQuery(sourcePoliciesQuery());
  const quality = useQuery(sourceQualityQuery());
  const qualityContract = useQuery(sourceQualityContractQuery());
  const report = quality.data?.report;
  const sourceSetting = useMutation({
    mutationFn: ({ sourceId, enabled }: { sourceId: string; enabled: boolean }) =>
      apiClient.updateSourceSetting(sourceId, enabled),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["source-policies"] });
    },
  });
  return (
    <section className="page-stack">
      <header className="page-heading">
        <p className="eyebrow">OSINT POLICY</p>
        <h1>Public-source boundaries</h1>
        <p className="lede">
          Provider capabilities and access modes come directly from the backend contract.
        </p>
      </header>
      <AsyncState
        loading={query.isLoading}
        error={query.error}
        empty={
          query.data?.providers.length === 0 && query.data.connectors.length === 0
        }
      >
        <section className="panel source-quality-panel" aria-labelledby="source-quality-heading">
          <div className="section-heading">
            <div>
              <p className="eyebrow">LIVE COVERAGE</p>
              <h2 id="source-quality-heading">Operational source quality</h2>
            </div>
            <span className={`status-pill ${report ? "enabled" : "optional"}`}>
              {report ? "Latest live report" : "Not run"}
            </span>
          </div>
          {!report ? (
            <EmptyState
              title="No gated live report on this installation"
              detail={quality.data?.message ?? "The API never starts network validation. Run the explicit local CLI gate to produce a report."}
            />
          ) : (
            <>
              <dl className="compact-facts source-quality-summary">
                <div><dt>Program / batch</dt><dd>{report.program_id} · {report.batch_id}</dd></div>
                <div><dt>Unknown hypotheses</dt><dd>{report.discovery_quality.unknown_hypotheses}</dd></div>
                <div><dt>Promotion eligible</dt><dd>{report.discovery_quality.promotion_eligible}</dd></div>
                <div><dt>Canonical writes</dt><dd>{report.discovery_quality.canonical_promotions}</dd></div>
                <div><dt>Stop reason</dt><dd>{report.stop_reason}</dd></div>
                <div><dt>Observed</dt><dd>{new Date(report.finished_at).toLocaleString()}</dd></div>
              </dl>
              <div className="table-scroll">
                <table className="evaluation-table source-quality-table">
                  <thead><tr><th>Source</th><th>Role</th><th>State</th><th>Requests</th><th>Yield</th><th>Accepted</th><th>Latency</th><th>Diagnostic</th></tr></thead>
                  <tbody>
                    {report.source_diagnostics.map((source) => (
                      <tr key={source.source_id}>
                        <td><strong>{source.source_id}</strong></td>
                        <td>{source.discovery_role}</td>
                        <td><span className={`status-pill ${source.state === "healthy" ? "enabled" : "optional"}`}>{source.state}</span></td>
                        <td>{source.requests}{source.retries ? ` (+${source.retries} retries)` : ""}</td>
                        <td>{source.results}</td>
                        <td>{source.accepted_observations}</td>
                        <td>{Math.round(source.latency_ms)} ms</td>
                        <td>{source.drift_reason ?? source.error ?? ((source.indexed_social_hosts?.length ?? 0) ? `Indexed: ${source.indexed_social_hosts?.join(", ")}` : "—")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="muted">{report.discovery_quality.ground_truth_note}</p>
            </>
          )}
          <p className="muted">
            {qualityContract.data?.required_gate ?? "Live validation requires an explicit local gate."}
            {" "}Direct LinkedIn/X/Instagram/Threads crawling remains disabled.
          </p>
        </section>
        <h2>Research providers</h2>
        <div className="source-policy-grid">
          {query.data?.providers.map((provider) => {
            const runtime = query.data.runtime?.find(
              (item) => item.source_id === provider.provider_id,
            );
            const enabled = runtime?.enabled ?? provider.default_enabled;
            return (
            <article className="source-policy-card" key={provider.provider_id}>
              <div className="card-title-row">
                <h2>{provider.display_name}</h2>
                <span className={`status-pill ${enabled ? "enabled" : "optional"}`}>
                  {enabled ? "Enabled" : "Disabled"}
                </span>
              </div>
              <dl>
                <div><dt>Access</dt><dd>{provider.access_method}</dd></div>
                <div><dt>Evidence role</dt><dd>{provider.evidence_role}</dd></div>
                <div><dt>Health</dt><dd>{runtime?.health?.replaceAll("_", " ") ?? "not checked"}</dd></div>
                <div><dt>Credentials</dt><dd>{provider.requires_credentials ? "Required" : "None"}</dd></div>
                <div><dt>Last checked</dt><dd>{runtime?.last_checked_at ? new Date(runtime.last_checked_at).toLocaleString() : "Never"}</dd></div>
              </dl>
              <div className="tag-row">
                {provider.capabilities.map((capability) => (
                  <span className="tag" key={capability}>{capability}</span>
                ))}
              </div>
              {provider.fallback ? <p className="muted">{provider.fallback}</p> : null}
              <button
                className="secondary-button"
                type="button"
                disabled={sourceSetting.isPending}
                onClick={() => sourceSetting.mutate({
                  sourceId: provider.provider_id,
                  enabled: !enabled,
                })}
              >
                {enabled ? "Disable source" : "Enable source"}
              </button>
            </article>
          );})}
        </div>
        <h2>Public intelligence watchlist</h2>
        <div className="source-policy-grid">
          {(query.data?.watchlists ?? []).map((item) => (
            <article className="source-policy-card" key={item.id}>
              <div className="card-title-row"><h2>{item.organization ?? item.name}</h2><span className="status-pill enabled">Ready</span></div>
              <p>{item.name}</p>
              {(item.aliases ?? []).length ? <p className="muted">Also known as {(item.aliases ?? []).join(", ")}</p> : null}
              <div className="tag-row">{(item.roles ?? []).map((role) => <span className="tag" key={role}>{role.replaceAll("_", " ").toLocaleLowerCase()}</span>)}</div>
            </article>
          ))}
        </div>
        <h2>Monitor connectors</h2>
        <div className="source-policy-grid">
          {query.data?.connectors.map((connector) => {
            const runtime = query.data.runtime?.find(
              (item) => item.source_id === connector.provider_id,
            );
            return (
            <article className="source-policy-card" key={connector.provider_id}>
              <div className="card-title-row">
                <h2>{connector.display_name}</h2>
                <span className={`status-pill ${runtime?.configuration_status === "configured" ? "enabled" : "optional"}`}>
                  {runtime?.configuration_status === "configured"
                    ? "Configured"
                    : runtime?.enabled ?? connector.default_enabled
                      ? "Enabled"
                      : "Restricted"}
                </span>
              </div>
              <dl>
                <div><dt>Access</dt><dd>{connector.access_method}</dd></div>
                <div><dt>Mode</dt><dd>{connector.access_mode}</dd></div>
                <div><dt>Evidence role</dt><dd>{connector.evidence_role}</dd></div>
                <div><dt>Configuration</dt><dd>{runtime?.configuration_status?.replaceAll("_", " ") ?? (connector.requires_credentials ? "not configured" : "not required")}</dd></div>
                <div><dt>Availability</dt><dd>{runtime?.health?.replaceAll("_", " ") ?? "not checked"}</dd></div>
                <div><dt>Credentials</dt><dd>{runtime?.credential_present === true ? "Present" : runtime?.credential_present === false ? "Absent" : connector.requires_credentials ? "Required" : "None"}</dd></div>
                <div><dt>Last live check</dt><dd>{runtime?.last_checked_at ? new Date(runtime.last_checked_at).toLocaleString() : "Never"}</dd></div>
              </dl>
              <div className="tag-row">
                {connector.capabilities.map((capability) => (
                  <span className="tag" key={capability}>{capability}</span>
                ))}
              </div>
              {connector.fallback ? <p className="muted">{connector.fallback}</p> : null}
            </article>
          );})}
        </div>
      </AsyncState>
      {quality.error || qualityContract.error ? (
        <PartialState>Live source-quality diagnostics are unavailable; policy metadata remains authoritative.</PartialState>
      ) : null}
      {!query.isLoading && !query.error && !query.data ? (
        <EmptyState detail="No source-policy response was returned." />
      ) : null}
    </section>
  );
}
