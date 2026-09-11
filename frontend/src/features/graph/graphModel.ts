import type {
  GraphSnapshotResponse,
  TemporalGraphEdge,
  TemporalGraphNode,
} from "../../api/client";

export interface GraphViewFilters {
  search: string;
  nodeType: string;
  edgeType: string;
  temporalStatus: string;
  hiddenNodeIds: ReadonlySet<string>;
}

export interface VisibleGraph {
  nodes: TemporalGraphNode[];
  edges: TemporalGraphEdge[];
}

export type Position3D = readonly [number, number, number];

export function neighborIds(
  nodeId: string,
  edges: TemporalGraphEdge[],
): Set<string> {
  const result = new Set<string>();
  for (const edge of edges) {
    if (edge.source_id === nodeId) result.add(edge.target_id);
    if (edge.target_id === nodeId) result.add(edge.source_id);
  }
  return result;
}

export function visibleGraph(
  snapshot: GraphSnapshotResponse,
  filters: GraphViewFilters,
): VisibleGraph {
  const term = filters.search.trim().toLocaleLowerCase();
  const matched = new Set(
    snapshot.nodes
      .filter((node) => !term || node.label.toLocaleLowerCase().includes(term))
      .map((node) => node.node_id),
  );
  if (term) {
    for (const nodeId of [...matched]) {
      for (const neighbor of neighborIds(nodeId, snapshot.edges)) matched.add(neighbor);
    }
  }
  const nodes = snapshot.nodes.filter(
    (node) =>
      !filters.hiddenNodeIds.has(node.node_id)
      && (!filters.nodeType || node.node_type === filters.nodeType)
      && (!term || matched.has(node.node_id)),
  );
  const nodeIds = new Set(nodes.map((node) => node.node_id));
  const edges = snapshot.edges.filter(
    (edge) =>
      nodeIds.has(edge.source_id)
      && nodeIds.has(edge.target_id)
      && (!filters.edgeType || edge.edge_type === filters.edgeType)
      && (!filters.temporalStatus || edge.temporal_status === filters.temporalStatus),
  );
  return { nodes, edges };
}

export function graphPositions(
  nodes: TemporalGraphNode[], edges: TemporalGraphEdge[] = [],
): Map<string, Position3D> {
  const positions = new Map<string, Position3D>();
  const count = Math.max(nodes.length, 1);
  const radius = Math.min(250, 72 + Math.sqrt(count) * 17);
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  const ordered = [...nodes].sort((a, b) => a.node_id.localeCompare(b.node_id));
  ordered.forEach((node, index) => {
    const y = 1 - (index / Math.max(count - 1, 1)) * 2;
    const ring = Math.sqrt(Math.max(0, 1 - y * y));
    const angle = goldenAngle * index;
    positions.set(node.node_id, [
      Math.cos(angle) * ring * radius,
      y * radius,
      Math.sin(angle) * ring * radius,
    ]);
  });
  if (nodes.length === 1) return new Map([[nodes[0]!.node_id, [0, 0, 0]]]);
  // ponytail: bounded 220-node view, 80 cooling steps, no perpetual animation.
  // Move to a worker/Barnes-Hut solver if the server view limit grows materially.
  type MutablePosition = [number, number, number];
  const axes = [0, 1, 2] as const;
  const points: MutablePosition[] = ordered.map((node) => [...positions.get(node.node_id)!]);
  const indices = new Map(ordered.map((node, index) => [node.node_id, index]));
  const links = [...edges].sort((a, b) => a.edge_id.localeCompare(b.edge_id));
  for (let step = 0; step < 80; step++) {
    const forces = points.map((point) => point.map((value) => -value * 0.008) as MutablePosition);
    for (let i = 0; i < points.length; i++) {
      for (let j = i + 1; j < points.length; j++) {
        const delta = axes.map((axis) => points[i]![axis] - points[j]![axis]);
        const distance = Math.max(1, Math.hypot(...delta));
        axes.forEach((axis) => {
          const value = delta[axis]!;
          const force = value * 850 / distance ** 3;
          forces[i]![axis] += force;
          forces[j]![axis] -= force;
        });
      }
    }
    for (const edge of links) {
      const i = indices.get(edge.source_id), j = indices.get(edge.target_id);
      if (i === undefined || j === undefined || i === j) continue;
      const delta = axes.map((axis) => points[j]![axis] - points[i]![axis]);
      const distance = Math.max(1, Math.hypot(...delta));
      axes.forEach((axis) => {
        const value = delta[axis]!;
        const force = value / distance * (distance - 75) * 0.08;
        forces[i]![axis] += force;
        forces[j]![axis] -= force;
      });
    }
    const temperature = 4 * (1 - step / 80);
    points.forEach((point, i) => axes.forEach((axis) => {
      point[axis] += Math.max(-temperature, Math.min(temperature, forces[i]![axis]));
    }));
  }
  ordered.forEach((node, index) => positions.set(node.node_id, points[index]!));
  return positions;
}
