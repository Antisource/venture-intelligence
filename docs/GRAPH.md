# Temporal knowledge graph

The M7 graph workspace is integrated into Research under the
`Network / relationships` view. Its authoritative JSON boundary is
`GET /api/v1/graph`; the older `/api/graph` and `/api/graph/{entity_id}`
endpoints remain compatibility APIs and do not drive the React workspace.

## Contract and integrity boundary

`GraphSnapshotResponse` bounds a response to at most 400 nodes, 800 edges, a
two-hop neighborhood, and 5,000 replay input events. Callers can request a
smaller limit, program scope, node types, minimum confidence, focus entity and
neighborhood depth. Nodes expose canonical type, label, confidence, program,
first observation, bounded properties and provenance. Edges expose type,
confidence, `observed_at`, explicit validity, temporal status/precision,
bounded properties and provenance. Source URLs are resolved from immutable
evidence event IDs; frontend code never creates facts.

The centralized materialized-identity gate runs before serialization. Live
person/company nodes must also resolve to validated canonical records. Generic,
polluted or malformed identities therefore cannot be recovered by graph search,
filtering or visualization.

## Historical semantics

No `as_of` parameter means a bounded read of the live materialized graph. An
aware `as_of` timestamp invokes the M2 isolated replay service and builds the
snapshot from eligible immutable events where `observed_at <= as_of`. The
response identifies `projection_source=isolated_replay` and returns the replay
projection digest. Replay does not read current derived graph state and cannot
mutate the live database. `observed_at` remains distinct from `valid_from` and
`valid_to`; unknown validity is never inferred from collection time.

## React workspace

The 3D view uses React Three Fiber, local Three.js and OrbitControls. It supports
rotate, pan, zoom, node selection/focus, search, program/type/relationship/
temporal/confidence filters, neighbor expand/collapse, node and edge inspectors,
provenance links, and live/as-of switching. The scene uses deterministic
positions, one line-segment geometry, demand rendering, capped device-pixel
ratio and the bounded API payload so it remains usable on normal laptops.

The relationship-table view is an accessible, non-WebGL equivalent with
selectable edges and the same confidence, observation, validity and evidence
information. It is the automatic fallback when WebGL is unavailable and can be
selected at any time.

The graph is also part of entity and research workflows rather than an isolated
visualization. Founder/company and CRM records deep-link with
`?focus=<entity_id>`; the backend applies the bounded focus contract and the
inspector links back to the typed intelligence profile. Research surfaces graph
navigation beside the current investigation while preserving server-provided
JSON as the only source of facts.

The product labels this workspace **Network** and keeps it beside Research and
the optional analyst. Its accessible relationship table consumes the same
filtered edge projection as the 3D scene, including confidence, temporal status,
and source provenance.

The 3D code is lazy-loaded only when Research's Network view selects the 3D
renderer. The former imperative graph renderer, legacy import map, and raw
`node_modules` server mounts were removed. The production build splits the
scene, controls, React Three Fiber, and Three.js core. Three.js 0.180's core
remains one 695 kB minified module (about 176 kB gzip); this upstream module
floor is reported by Vite's 500 kB raw chunk warning, while the table fallback
loads none of it. No CDN or remote JavaScript is used.
