import { Canvas, useThree, type ThreeEvent } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

import type { TemporalGraphNode, TemporalGraphEdge } from "../../api/client";
import { graphPositions, type Position3D, type VisibleGraph } from "./graphModel";

const NODE_COLORS: Record<string, string> = {
  Company: "#55d6be",
  Person: "#7da7ff",
  Accelerator: "#ffc56e",
  Organization: "#bd8cff",
  Community: "#bd8cff",
  Repository: "#ff8d8d",
  ResearchPaper: "#ff9dd2",
  JobPosting: "#a5e887",
  University: "#77c7f2",
  Employer: "#d9a36a",
  Grant: "#f3aa7f",
};

function CameraControls({ focus }: { focus?: Position3D }) {
  const { camera, gl, invalidate } = useThree();
  const controlsRef = useRef<OrbitControls | null>(null);
  useEffect(() => {
    const controls = new OrbitControls(camera, gl.domElement);
    controlsRef.current = controls;
    controls.enableDamping = false;
    controls.minDistance = 80;
    controls.maxDistance = 1_400;
    const handleChange = () => invalidate();
    controls.addEventListener("change", handleChange);
    return () => {
      controls.removeEventListener("change", handleChange);
      controls.dispose();
      controlsRef.current = null;
    };
  }, [camera, gl, invalidate]);
  useEffect(() => {
    if (!focus) return;
    camera.position.set(focus[0] + 95, focus[1] + 75, focus[2] + 170);
    camera.lookAt(focus[0], focus[1], focus[2]);
    controlsRef.current?.target.set(...focus);
    controlsRef.current?.update();
    camera.updateProjectionMatrix();
    invalidate();
  }, [camera, focus, invalidate]);
  return null;
}

function EdgeSegments({ graph, positions, color, onSelect, onHover }: {
  graph: VisibleGraph;
  positions: Map<string, Position3D>;
  color: string;
  onSelect: (edge: TemporalGraphEdge) => void;
  onHover: (text: string) => void;
}) {
  const segments = useMemo(() => {
    const coordinates: number[] = [];
    for (const edge of graph.edges) {
      const source = positions.get(edge.source_id);
      const target = positions.get(edge.target_id);
      if (source && target) {
        const direction = new THREE.Vector3(...target).sub(new THREE.Vector3(...source)).normalize();
        const tip = new THREE.Vector3(...target).addScaledVector(direction, -10);
        const base = tip.clone().addScaledVector(direction, -7);
        const side = new THREE.Vector3().crossVectors(direction, Math.abs(direction.y) < 0.9 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(1, 0, 0)).normalize().multiplyScalar(3);
        coordinates.push(...source, ...tip.toArray(), ...tip.toArray(), ...base.clone().add(side).toArray(), ...tip.toArray(), ...base.sub(side).toArray());
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(coordinates, 3));
    const material = new THREE.LineBasicMaterial({
      color,
      transparent: true,
      opacity: 0.75,
    });
    return new THREE.LineSegments(geometry, material);
  }, [color, graph.edges, positions]);
  useEffect(() => () => {
    segments.geometry.dispose();
    if (Array.isArray(segments.material)) {
      segments.material.forEach((material) => material.dispose());
    } else {
      segments.material.dispose();
    }
  }, [segments]);
  const hit = (event: ThreeEvent<MouseEvent>) => graph.edges[Math.floor((event.index ?? -6) / 6)];
  return <primitive object={segments}
    onClick={(event: ThreeEvent<MouseEvent>) => { const edge = hit(event); if (edge) { event.stopPropagation(); onSelect(edge); } }}
    onPointerOver={(event: ThreeEvent<PointerEvent>) => { const edge = hit(event); if (edge) onHover(`${edge.edge_type.replaceAll("_", " ")} · ${Math.round(edge.confidence * 100)}% confidence`); }}
    onPointerOut={() => onHover("")}
  />;
}

function NodeMesh({
  node,
  position,
  selected,
  onSelect,
  onHover,
}: {
  node: TemporalGraphNode;
  position: Position3D;
  selected: boolean;
  onSelect: (nodeId: string) => void;
  onHover: (text: string) => void;
}) {
  return (
    <mesh
      position={position}
      scale={selected ? 1.35 : 1}
      onPointerOver={(event) => { event.stopPropagation(); onHover(`${node.label} · ${node.node_type}`); }}
      onPointerOut={() => onHover("")}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(node.node_id);
      }}
    >
      <sphereGeometry args={[7.5, 14, 10]} />
      <meshStandardMaterial
        color={NODE_COLORS[node.node_type] ?? "#aab7c4"}
        emissive={selected ? "#ffffff" : "#000000"}
        emissiveIntensity={selected ? 0.28 : 0}
        roughness={0.62}
        metalness={0.08}
      />
    </mesh>
  );
}

export function GraphScene({
  graph,
  selectedNodeId,
  onSelectNode,
  onSelectEdge,
  theme,
}: {
  graph: VisibleGraph;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
  onSelectEdge: (edge: TemporalGraphEdge) => void;
  theme: "light" | "dark";
}) {
  const positions = useMemo(() => graphPositions(graph.nodes, graph.edges), [graph.nodes, graph.edges]);
  const focus = selectedNodeId ? positions.get(selectedNodeId) : undefined;
  const [hover, setHover] = useState("");
  return (
    <div
      className="temporal-graph-canvas"
      role="img"
      aria-label={`Interactive 3D relationship graph with ${graph.nodes.length} nodes and ${graph.edges.length} edges`}
    >
      <Canvas
        camera={{ position: [0, 0, 440], fov: 48, near: 0.1, far: 2_000 }}
        dpr={[1, 1.5]}
        frameloop="demand"
        gl={{ antialias: true, powerPreference: "high-performance" }}
      >
        <color attach="background" args={[theme === "dark" ? "#0c110e" : "#f5f8f5"]} />
        <ambientLight intensity={1.8} />
        <directionalLight position={[120, 180, 260]} intensity={1.4} />
        <EdgeSegments graph={graph} positions={positions} color={theme === "dark" ? "#8fa1b4" : "#46564e"} onSelect={onSelectEdge} onHover={setHover} />
        {graph.nodes.map((node) => (
          <NodeMesh
            key={node.node_id}
            node={node}
            position={positions.get(node.node_id) ?? [0, 0, 0]}
            selected={node.node_id === selectedNodeId}
            onSelect={onSelectNode}
            onHover={setHover}
          />
        ))}
        <CameraControls focus={focus} />
      </Canvas>
      <output className="graph-hover-details" aria-live="polite">{hover || "Drag to orbit · right-drag to pan · scroll to zoom · select a node or arrow"}</output>
    </div>
  );
}
