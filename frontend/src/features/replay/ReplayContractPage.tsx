import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { replayContractQuery } from "../../api/queries";
import { AsyncState } from "../../components/AsyncState";
import { Icon } from "../../components/Icon";

export function ReplayContractPage() {
  const replay = useQuery(replayContractQuery());

  return (
    <section className="page-stack">
      <header className="page-heading">
        <p className="eyebrow">HISTORICAL REPLAY</p>
        <h1>Reconstruct what the system knew then.</h1>
        <p className="lede">
          Historical reasoning uses an isolated store and immutable events, so current state cannot leak backward or be mutated by replay.
        </p>
      </header>
      <AsyncState loading={replay.isLoading} error={replay.error}>
        <div className="replay-workspace">
          <section className="panel replay-model"><div className="section-heading"><div><p className="eyebrow">REASONING MODEL</p><h2>An auditable path through time</h2></div><div className="tag-row"><span className="status-pill">Read only</span><span className="tag">{replay.data?.default_mode}</span></div></div>
            <ol className="replay-steps"><li><span>1</span><div><strong>Choose a cutoff</strong><p>Default semantics include only knowledge with {replay.data?.default_mode} on or before that instant.</p></div></li><li><span>2</span><div><strong>Rebuild projections</strong><p>Immutable events are replayed into a disposable {replay.data?.isolated_store.replaceAll("_", " ")} store.</p></div></li><li><span>3</span><div><strong>Compare outcomes</strong><p>Deterministic digests make identical inputs reproducible and evaluation-ready.</p></div></li></ol>
            <div className="command-card"><span>Local bounded command</span><code>{replay.data?.cli_command}</code></div>
          </section>
          <aside className="replay-rail">
            <section className="panel"><p className="eyebrow">SAFETY BOUNDARY</p><h2>No live side effects</h2><div className="tag-row">{replay.data?.side_effects_suppressed.map((effect) => <span className="tag" key={effect}>{effect.replaceAll("_", " ")}</span>)}</div><dl className="compact-facts"><div><dt>Remote execution</dt><dd>{replay.data?.remote_execution_enabled ? "Enabled" : "Disabled"}</dd></div><div><dt>Event cap</dt><dd>{replay.data?.max_events.toLocaleString()}</dd></div></dl></section>
            <section className="panel"><Icon name="evaluate" /><h2>Inspect evaluation runs</h2><p>Use replay-backed diagnostics to understand misses, false positives, and lead time.</p><Link className="button secondary-button" to="/evaluate">Open Evaluate <Icon name="arrow" /></Link></section>
          </aside>
        </div>
      </AsyncState>
    </section>
  );
}
