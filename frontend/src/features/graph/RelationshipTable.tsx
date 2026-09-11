import type { TemporalGraphEdge, TemporalGraphNode } from "../../api/client";
import type { VisibleGraph } from "./graphModel";

function percent(value: number | null | undefined) {
  return value === null || value === undefined ? "Unknown" : `${Math.round(value * 100)}%`;
}

function words(value: string) {
  return value.replaceAll("_", " ").toLocaleLowerCase();
}

export function RelationshipTable({
  graph,
  onSelectEdge,
  onSelectNode,
}: {
  graph: VisibleGraph;
  onSelectEdge: (edge: TemporalGraphEdge) => void;
  onSelectNode: (nodeId: string) => void;
}) {
  const nodes = new Map<string, TemporalGraphNode>(
    graph.nodes.map((node) => [node.node_id, node]),
  );
  return (
    <div className="table-scroll relationship-fallback" aria-label="Relationship table">
      <table className="evaluation-table">
        <thead>
          <tr>
            <th>Source</th>
            <th>Relationship</th>
            <th>Target</th>
            <th>Confidence</th>
            <th>Observed</th>
            <th>Validity</th>
            <th>Evidence</th>
          </tr>
        </thead>
        <tbody>
          {graph.edges.map((edge) => (
            <tr key={edge.edge_id}>
              <td>
                <button
                  type="button"
                  className="relationship-link"
                  onClick={() => onSelectNode(edge.source_id)}
                >
                  {nodes.get(edge.source_id)?.label ?? edge.source_id}
                </button>
              </td>
              <td>
                <button
                  type="button"
                  className="relationship-link"
                  onClick={() => onSelectEdge(edge)}
                >
                  {words(edge.edge_type)}
                </button>
              </td>
              <td>
                <button
                  type="button"
                  className="relationship-link"
                  onClick={() => onSelectNode(edge.target_id)}
                >
                  {nodes.get(edge.target_id)?.label ?? edge.target_id}
                </button>
              </td>
              <td>{percent(edge.confidence)}</td>
              <td>{new Date(edge.observed_at).toLocaleDateString()}</td>
              <td>
                {words(edge.temporal_status)}
              </td>
              <td>{edge.provenance?.source_urls?.[0] ? <a href={edge.provenance.source_urls[0]} target="_blank" rel="noreferrer">Open source</a> : `${edge.provenance?.evidence_ids?.length ?? 0} record(s)`}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!graph.edges.length ? (
        <p className="state-empty">No relationships match the current filters.</p>
      ) : null}
    </div>
  );
}
