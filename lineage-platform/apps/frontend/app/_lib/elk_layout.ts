/**
 * ELK-based layered DAG layout.
 *
 * Replaces the previous force / dagre / star pipelines on the lineage and
 * explorer canvases. We run ``elkjs`` directly (rather than the
 * ``cytoscape-elk`` plugin) for two reasons:
 *
 *   1. Cytoscape's ``preset`` layout consumes the {x,y} we hand it without
 *      auto-running anything — predictable, debuggable, no race with React.
 *   2. ELK returns routed edge sections (start + bends + end). We pipe those
 *      into ``elkEdgeToPath`` from ``edge_routing.ts`` to draw obstacle-
 *      avoiding orthogonal SVG paths on top of the Cytoscape canvas.
 */
import ELK from "elkjs/lib/elk.bundled.js";
import type { GraphPayload } from "./api";
import {
  ELK_LAYOUT_OPTIONS,
  elkEdgeToPath,
  type ElkEdgeSection,
  type Point,
} from "./edge_routing";

const elk = new ELK();

export interface NodeSize {
  width: number;
  height: number;
}

export type NodeSizer = (
  nodeId: string,
  label: string,
  data?: any,
) => NodeSize;

export interface ElkLayoutOptions {
  direction?: "RIGHT" | "DOWN";
  /** Per-node size measurement. Defaults to plain rectangles. */
  sizer?: NodeSizer;
  /** Corner fillet radius for the SVG bend points. 0 = sharp right angles. */
  edgeRadius?: number;
}

export interface ElkLayoutResult {
  /** Centre coords keyed by node id (Cytoscape's preset uses centre, not top-left). */
  positions: Map<string, Point>;
  /** SVG path `d` strings keyed by edge id, ready to drop into <path d=...>. */
  edgePaths: Map<string, string>;
  /** Top-left corner positions, useful for the manual obstacle-avoidance fallback. */
  rects: Map<string, { x: number; y: number; width: number; height: number }>;
  /** Total content bounding box (mainly for the minimap). */
  bounds: { width: number; height: number };
}

const DEFAULT_NODE_SIZE: NodeSize = { width: 200, height: 88 };
const COMPACT_NODE_SIZE: NodeSize = { width: 160, height: 56 };
const SUMMARY_NODE_SIZE: NodeSize = { width: 180, height: 56 };

const COMPACT_LABELS = new Set([
  "Attribute",
  "UDF",
  "Connection",
  "Parameter",
  "Variable",
  "Resource",
  "FileWatcher",
]);

/**
 * Default sizer matches the existing Cytoscape stylesheet so the boxes ELK
 * lays out are the same dimensions Cytoscape renders. Mismatches here would
 * push the SVG bend points off the visible node edges.
 *
 * Reads ``data._size`` first when present — that's the dimension
 * ``prepare_graph`` pre-computed from the actual label lines via
 * ``computeNodeSize``. Falls back to fixed buckets only when called
 * without node data (e.g. legacy callers).
 */
export const defaultNodeSizer: NodeSizer = (_id, label, data) => {
  if (
    data &&
    typeof data._size?.width === "number" &&
    typeof data._size?.height === "number"
  ) {
    return { width: data._size.width, height: data._size.height };
  }
  if (label === "__summary__") return SUMMARY_NODE_SIZE;
  if (COMPACT_LABELS.has(label)) return COMPACT_NODE_SIZE;
  return DEFAULT_NODE_SIZE;
};

export async function computeElkLayout(
  data: GraphPayload,
  opts: ElkLayoutOptions = {},
): Promise<ElkLayoutResult> {
  const direction = opts.direction ?? "RIGHT";
  const sizer = opts.sizer ?? defaultNodeSizer;
  const edgeRadius = opts.edgeRadius ?? 8;

  const elkNodes = data.nodes.map((n) => {
    const size = sizer(n.data.id, n.data.label, n.data);
    return {
      id: n.data.id,
      width: size.width,
      height: size.height,
    };
  });

  // ELK is strict: duplicate edge ids cause it to throw. dedupe in case the
  // upstream payload includes duplicates from multi-hop joins.
  const seenEdgeIds = new Set<string>();
  const elkEdges = [];
  for (const e of data.edges) {
    if (seenEdgeIds.has(e.data.id)) continue;
    seenEdgeIds.add(e.data.id);
    elkEdges.push({
      id: e.data.id,
      sources: [e.data.source],
      targets: [e.data.target],
    });
  }

  const layoutInput = {
    id: "root",
    layoutOptions: {
      ...ELK_LAYOUT_OPTIONS,
      "elk.direction": direction,
    },
    children: elkNodes,
    edges: elkEdges,
  };

  let result: any;
  try {
    result = await elk.layout(layoutInput);
  } catch (err) {
    // ELK can fail on degenerate inputs (e.g. only one node). Fall back to a
    // single-point layout so the canvas still renders.
    // eslint-disable-next-line no-console
    console.warn("ELK layout failed; falling back to centred preset", err);
    const positions = new Map<string, Point>();
    data.nodes.forEach((n, i) => positions.set(n.data.id, { x: i * 240, y: 0 }));
    return {
      positions,
      edgePaths: new Map(),
      rects: new Map(),
      bounds: { width: Math.max(1, data.nodes.length * 240), height: 200 },
    };
  }

  const positions = new Map<string, Point>();
  const rects = new Map<string, { x: number; y: number; width: number; height: number }>();
  for (const child of result.children ?? []) {
    const w = child.width ?? 200;
    const h = child.height ?? 88;
    const x = child.x ?? 0;
    const y = child.y ?? 0;
    // ELK x/y are top-left corners; Cytoscape preset expects the node CENTRE.
    positions.set(child.id, { x: x + w / 2, y: y + h / 2 });
    rects.set(child.id, { x, y, width: w, height: h });
  }

  const edgePaths = new Map<string, string>();
  for (const edge of result.edges ?? []) {
    const sections = (edge.sections ?? []) as ElkEdgeSection[];
    if (sections.length === 0) continue;
    edgePaths.set(edge.id, elkEdgeToPath({ sections }, edgeRadius));
  }

  return {
    positions,
    edgePaths,
    rects,
    bounds: {
      width: result.width ?? 0,
      height: result.height ?? 0,
    },
  };
}
