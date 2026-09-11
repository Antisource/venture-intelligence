import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import type { TemporalGraphEdge, TemporalGraphNode } from "../../api/client";
import { graphQuery } from "../../api/queries";
import { ErrorState, LoadingState } from "../../components/AsyncState";
import { Icon } from "../../components/Icon";
import { usePrograms } from "../../state/ProgramProvider";
import { useTheme } from "../../theme/ThemeProvider";
import { neighborIds, visibleGraph } from "./graphModel";
import { RelationshipTable } from "./RelationshipTable";
import "./graph.css";

const GraphScene = lazy(() => import("./GraphScene").then((module) => ({
  default: module.GraphScene,
})));

function percent(value: number | null | undefined) {
  return value === null || value === undefined ? "Unknown" : `${Math.round(value * 100)}%`;
}

function words(value: string) {
  return value.replaceAll("_", " ").toLocaleLowerCase();
}

function toUtc(value: string): string | undefined {
  if (!value) return undefined;
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? undefined : parsed.toISOString();
}

function SourceLinks({ urls = [] }: { urls?: string[] }) {
  if (!urls.length) return <p className="state-empty">No source URL attached.</p>;
  return (
    <ul className="graph-source-list">
      {urls.map((url) => (
        <li key={url}>
          <a href={url} target="_blank" rel="noreferrer">{url}</a>
        </li>
      ))}
    </ul>
  );
}

function NodeInspector({ node }: { node: TemporalGraphNode }) {
  const intelligenceRoute = node.node_type.toLocaleLowerCase() === "person" ? "founders" : node.node_type.toLocaleLowerCase() === "company" ? "companies" : null;
  return (
    <article className="graph-inspector-card">
      <p className="eyebrow">ENTITY</p>
      <h2>{node.label}</h2>
      <p>{words(node.node_type)} · {percent(node.confidence)} confidence</p>
      <dl>
        <div><dt>Program</dt><dd>{node.program_id ?? "Unspecified"}</dd></div>
        <div><dt>First observed</dt><dd>{node.first_observed_at ? new Date(node.first_observed_at).toLocaleString() : "Unknown"}</dd></div>
      </dl>
      <details>
        <summary>Technical details</summary>
        <p>{node.provenance?.evidence_ids?.length ?? 0} linked evidence records</p>
        <pre>{JSON.stringify(node.properties ?? {}, null, 2)}</pre>
      </details>
      <h3>Provenance</h3>
      <SourceLinks urls={node.provenance?.source_urls} />
      {intelligenceRoute ? <Link className="button secondary-button" to={`/research/${encodeURIComponent(node.node_id)}`}>Open intelligence profile <Icon name="arrow" /></Link> : null}
    </article>
  );
}

function EdgeInspector({ edge, nodes }: {
  edge: TemporalGraphEdge;
  nodes: Map<string, TemporalGraphNode>;
}) {
  return (
    <article className="graph-inspector-card">
      <p className="eyebrow">RELATIONSHIP</p>
      <h2>{words(edge.edge_type)}</h2>
      <p>{nodes.get(edge.source_id)?.label ?? edge.source_id} → {nodes.get(edge.target_id)?.label ?? edge.target_id}</p>
      <dl>
        <div><dt>Confidence</dt><dd>{percent(edge.confidence)}</dd></div>
        <div><dt>Observed</dt><dd>{new Date(edge.observed_at).toLocaleString()}</dd></div>
        <div><dt>Status</dt><dd>{words(edge.temporal_status)}</dd></div>
        <div><dt>Valid from</dt><dd>{edge.validity.valid_from ? new Date(edge.validity.valid_from).toLocaleString() : "Unknown"}</dd></div>
        <div><dt>Valid to</dt><dd>{edge.validity.valid_to ? new Date(edge.validity.valid_to).toLocaleString() : "Open / unknown"}</dd></div>
      </dl>
      <details><summary>Technical details</summary><p>{edge.validity_precision} precision · {edge.provenance?.evidence_ids?.length ?? 0} linked evidence records</p></details>
      <h3>Provenance</h3>
      <SourceLinks urls={edge.provenance?.source_urls} />
    </article>
  );
}

export function GraphPage({ focus }: { focus?: string } = {}) {
  const { selectedProgram } = usePrograms();
  const theme = useTheme();
  const [parameters] = useSearchParams();
  const focusId = focus ?? parameters.get("focus") ?? undefined;
  const supports3D = typeof WebGLRenderingContext !== "undefined";
  const [view, setView] = useState<"3d" | "table">(supports3D ? "3d" : "table");
  const [search, setSearch] = useState("");
  const [nodeType, setNodeType] = useState("");
  const [edgeType, setEdgeType] = useState("");
  const [temporalStatus, setTemporalStatus] = useState("");
  const [minimumConfidence, setMinimumConfidence] = useState(0);
  const [asOfDraft, setAsOfDraft] = useState("");
  const [asOf, setAsOf] = useState<string | undefined>();
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(focusId ?? null);
  const [selectedEdge, setSelectedEdge] = useState<TemporalGraphEdge | null>(null);
  const [hiddenNodeIds, setHiddenNodeIds] = useState<Set<string>>(new Set());
  const query = useQuery(graphQuery({
    asOf,
    programId: selectedProgram || undefined,
    minConfidence: minimumConfidence,
    focusId,
    maxNodes: 220,
    maxEdges: 440,
  }));
  const snapshot = query.data;
  const graph = useMemo(
    () => snapshot ? visibleGraph(snapshot, {
      search,
      nodeType,
      edgeType,
      temporalStatus,
      hiddenNodeIds,
    }) : { nodes: [], edges: [] },
    [edgeType, hiddenNodeIds, nodeType, search, snapshot, temporalStatus],
  );
  const verifiedEntityCount = graph.nodes.filter((node) => (
    ["company", "person", "founder"].includes(node.node_type.toLocaleLowerCase())
  )).length;
  const nodes = useMemo(
    () => new Map(graph.nodes.map((node) => [node.node_id, node])),
    [graph.nodes],
  );
  const selectedNode = selectedNodeId ? nodes.get(selectedNodeId) ?? null : null;
  const selectedNeighbors = selectedNodeId && snapshot
    ? neighborIds(selectedNodeId, snapshot.edges)
    : new Set<string>();
  const neighborsCollapsed = [...selectedNeighbors].some((nodeId) => hiddenNodeIds.has(nodeId));

  const focusSearch = () => {
    const term = search.trim().toLocaleLowerCase();
    const match = snapshot?.nodes.find((node) => node.label.toLocaleLowerCase().includes(term));
    if (match) {
      setSelectedNodeId(match.node_id);
      setSelectedEdge(null);
    }
  };
  const toggleNeighbors = () => {
    if (!selectedNodeId || !snapshot) return;
    const related = neighborIds(selectedNodeId, snapshot.edges);
    setHiddenNodeIds((current) => {
      const next = new Set(current);
      const collapsed = [...related].some((nodeId) => next.has(nodeId));
      for (const nodeId of related) {
        if (collapsed) next.delete(nodeId);
        else next.add(nodeId);
      }
      return next;
    });
  };

  return (
    <section className="page-stack temporal-graph-page">
      <header className="page-heading graph-heading">
        <div>
          <p className="eyebrow">RELATIONSHIP INTELLIGENCE</p>
          <h2>Network / relationships</h2>
          <p className="lede">Inspect evidence-backed relationships, now or at a historical cutoff.</p>
        </div>
        <div className="graph-view-toggle" role="group" aria-label="Graph view">
          <button type="button" className={view === "3d" ? "selected" : ""} disabled={!supports3D} onClick={() => setView("3d")}>3D</button>
          <button type="button" className={view === "table" ? "selected" : ""} onClick={() => setView("table")}>Relationship table</button>
        </div>
      </header>

      <div className="graph-filter-panel">
        <label>Search
          <span className="graph-search-row">
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Company, founder, repository…" list="graph-node-options" />
            <button type="button" onClick={focusSearch}>Focus</button>
          </span>
        </label>
        <datalist id="graph-node-options">
          {snapshot?.nodes.map((node) => <option key={node.node_id} value={node.label} />)}
        </datalist>
        <label>Node type
          <select value={nodeType} onChange={(event) => setNodeType(event.target.value)}>
            <option value="">All node types</option>
            {snapshot?.available_node_types?.map((item) => <option key={item}>{item}</option>)}
          </select>
        </label>
        <label>Relationship
          <select value={edgeType} onChange={(event) => setEdgeType(event.target.value)}>
            <option value="">All relationships</option>
            {snapshot?.available_edge_types?.map((item) => <option key={item}>{item}</option>)}
          </select>
        </label>
        <label>Temporal status
          <select value={temporalStatus} onChange={(event) => setTemporalStatus(event.target.value)}>
            <option value="">All statuses</option>
            {(["current", "historical", "stale", "contradicted", "unknown"] as const).map((item) => <option key={item}>{item}</option>)}
          </select>
        </label>
        <label>Minimum confidence · {percent(minimumConfidence)}
          <input type="range" min="0" max="1" step="0.05" value={minimumConfidence} onChange={(event) => setMinimumConfidence(Number(event.target.value))} />
        </label>
        <label>As of
          <span className="graph-search-row">
            <input type="datetime-local" value={asOfDraft} onChange={(event) => setAsOfDraft(event.target.value)} />
            <button type="button" onClick={() => setAsOf(toUtc(asOfDraft))}>Apply</button>
            <button type="button" className="secondary-button" onClick={() => { setAsOfDraft(""); setAsOf(undefined); }}>Live</button>
          </span>
        </label>
      </div>

      {query.isLoading ? <LoadingState label="Building bounded graph projection…" /> : null}
      {query.error ? <ErrorState error={query.error} /> : null}
      {snapshot ? (
        <>
          <div className="graph-status-strip" aria-live="polite">
            <span>{graph.nodes.length} / {snapshot.total_nodes} nodes</span>
            <span>{graph.edges.length} / {snapshot.total_edges} relationships</span>
            <span>{snapshot.projection_source === "isolated_replay" ? "Isolated replay" : "Live projection"}</span>
            <span>As of {new Date(snapshot.as_of).toLocaleString()}</span>
            {snapshot.truncated ? <strong>Bounded result — refine filters to inspect more.</strong> : null}
          </div>
          {!verifiedEntityCount ? <div className="state-card state-empty"><strong>No verified entities exist yet.</strong><span>Provider, program, or search labels are not promoted into graph identities.</span><Link className="button" to="/discovery">Return to Discovery</Link></div> : null}
          {verifiedEntityCount > 0 && snapshot.edges.length === 0 ? <div className="state-card state-empty"><strong>No verified relationships yet.</strong><span>This identity has no evidence-backed canonical edges. Ambiguous and unbound leads never create graph relationships.</span><Link className="button" to="/research">Continue research</Link></div> : null}
          {snapshot.edges.length > 0 ? <div className="graph-workbench">
            <div className="graph-stage">
              {view === "3d" && supports3D ? (
                <Suspense fallback={<LoadingState label="Preparing WebGL scene…" />}>
                  <GraphScene graph={graph} selectedNodeId={selectedNodeId} theme={theme.resolved} onSelectNode={(nodeId) => { setSelectedNodeId(nodeId); setSelectedEdge(null); }} onSelectEdge={(edge) => { setSelectedEdge(edge); setSelectedNodeId(null); }} />
                </Suspense>
              ) : (
                <RelationshipTable
                  graph={graph}
                  onSelectEdge={(edge) => { setSelectedEdge(edge); setSelectedNodeId(null); }}
                  onSelectNode={(nodeId) => { setSelectedNodeId(nodeId); setSelectedEdge(null); }}
                />
              )}
            </div>
            <aside className="graph-inspector" aria-label="Graph inspector">
              {selectedNode ? (
                <>
                  <button type="button" className="secondary-button" onClick={toggleNeighbors}>{neighborsCollapsed ? "Expand neighbors" : "Collapse neighbors"}</button>
                  <NodeInspector node={selectedNode} />
                </>
              ) : selectedEdge ? (
                <EdgeInspector edge={selectedEdge} nodes={nodes} />
              ) : (
                <div className="state-empty"><h2>Inspect the graph</h2><p>Select a 3D node or a relationship in the table to inspect confidence, time semantics, and provenance.</p></div>
              )}
            </aside>
          </div> : null}
          <details className="graph-semantics panel">
            <summary>Temporal and performance contract</summary>
            <p>{snapshot.semantics}</p>
            <p>Payload cap: {snapshot.limits.max_nodes} nodes, {snapshot.limits.max_edges} edges, neighborhood depth {snapshot.limits.max_depth}, replay input cap {snapshot.limits.max_replay_events} events.</p>
            {snapshot.replay_projection_digest ? <code>{snapshot.replay_projection_digest}</code> : null}
          </details>
        </>
      ) : null}
    </section>
  );
}

export default GraphPage;
