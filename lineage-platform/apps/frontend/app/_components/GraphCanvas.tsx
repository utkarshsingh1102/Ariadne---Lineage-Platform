"use client";

import cytoscape, { Core, ElementDefinition } from "cytoscape";
import dagre from "cytoscape-dagre";
import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphPayload } from "../_lib/api";
import { cytoscapeStyles } from "../_lib/cytoscape-config";
import {
  computeElkLayout,
  type ElkLayoutResult,
} from "../_lib/elk_layout";
import {
  prepareGraphData,
  type ColumnMeta,
} from "../_lib/prepare_graph";
import { capFanout } from "../_lib/fanout_cap";
import { GraphMinimap } from "./GraphMinimap";
import { GraphToolbar } from "./GraphToolbar";

if (typeof window !== "undefined") {
  try {
    cytoscape.use(dagre);
  } catch {
    /* hot reload */
  }
}

export interface NodeTapDetail {
  id: string;
  data: any;
  /** Columns owned by this node, when it's a Table or DataFrame. */
  columns?: ColumnMeta[];
}

interface Props {
  data: GraphPayload;
  onNodeTap?: (nodeId: string, detail: NodeTapDetail) => void;
  /** Imperative focus channel: bumping the token centres + pulses the node. */
  focusToken?: { id: string; ts: number } | null;
  /** If set, treat as the lineage start node — pre-selects it and gives it the star-center ring. */
  centerNodeId?: string | null;
}

const BADGE_OFFSET = 110;
const SVG_PADDING = 80;

export function GraphCanvas({
  data,
  onNodeTap,
  focusToken,
  centerNodeId,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cytoMountRef = useRef<HTMLDivElement | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const cyRef = useRef<Core | null>(null);

  // -- Type filter (drives visibleData below). Attribute defaults OFF so the
  //    canvas isn't drowned in column boxes; user can re-enable it.
  const allTypes = useMemo(() => {
    const seen = new Set<string>();
    for (const n of data.nodes) {
      if (n.data.label) seen.add(n.data.label);
    }
    return Array.from(seen).sort();
  }, [data]);
  const [visibleTypes, setVisibleTypes] = useState<Set<string>>(() => {
    const s = new Set(allTypes);
    s.delete("Attribute");
    s.delete("Column");
    s.delete("Field");
    return s;
  });
  useEffect(() => {
    setVisibleTypes((prev) => {
      const merged = new Set(prev);
      for (const t of allTypes) {
        if (!merged.has(t) && t !== "Attribute" && t !== "Column" && t !== "Field") {
          merged.add(t);
        }
      }
      // Drop labels that aren't in the payload anymore.
      for (const t of [...merged]) {
        if (!allTypes.includes(t)) merged.delete(t);
      }
      return merged;
    });
  }, [allTypes]);

  // Expanded summaries — when the user taps "+13 DataFrames", we drop the
  // cap on (sourceId::label) so the hidden members render fully on the next
  // layout pass.
  const [expandedSummaries, setExpandedSummaries] = useState<Set<string>>(
    () => new Set(),
  );
  useEffect(() => setExpandedSummaries(new Set()), [data]);

  // -- Prepare data: drop attributes (default), build column index, then cap
  //    fan-out so ELK never sees a 6000-px-wide tier.
  const { renderData, columnsByOwnerId } = useMemo(() => {
    const includeAttrs =
      visibleTypes.has("Attribute") ||
      visibleTypes.has("Column") ||
      visibleTypes.has("Field");
    const filter = new Set(visibleTypes);
    const { visibleData, columnsByOwnerId } = prepareGraphData(data, {
      includeAttributes: includeAttrs,
      typeFilter: filter,
    });
    const capped = capFanout(visibleData, { expanded: expandedSummaries });
    return {
      renderData: capped.data,
      columnsByOwnerId,
    };
  }, [data, visibleTypes, expandedSummaries]);

  // -- Run ELK layout asynchronously. We store the latest result in a state
  //    so React rerenders the SVG overlay together with Cytoscape.
  const [layout, setLayout] = useState<ElkLayoutResult | null>(null);
  const layoutRef = useRef<ElkLayoutResult | null>(null);
  layoutRef.current = layout;

  useEffect(() => {
    let cancelled = false;
    computeElkLayout(renderData, { direction: "RIGHT", edgeRadius: 8 }).then(
      (result) => {
        if (cancelled) return;
        setLayout(result);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [renderData]);

  // Stable refs for callbacks so cytoscape isn't rebuilt on parent re-render.
  const onNodeTapRef = useRef(onNodeTap);
  onNodeTapRef.current = onNodeTap;
  const columnsByOwnerRef = useRef(columnsByOwnerId);
  columnsByOwnerRef.current = columnsByOwnerId;

  // -- Cytoscape lifecycle. The graph only rebuilds when the layout result or
  //    the rendered payload changes; visual class swaps go through cy refs.
  useEffect(() => {
    if (!cytoMountRef.current || !layout) return;

    const positions = layout.positions;

    // Synthetic badges for any node that still has hidden neighbours — but
    // the cap pass already turned over-cap groups into summary nodes, so
    // badges are now only used for the centerNodeId-driven progressive
    // disclosure if a future caller passes it.
    const badgeNodes: ElementDefinition[] = [];
    const badgeEdges: ElementDefinition[] = [];

    const elements: ElementDefinition[] = [
      ...renderData.nodes.map((n) => {
        const pos = positions.get(n.data.id);
        const base: any = { group: "nodes" as const, data: n.data };
        if (pos) base.position = { x: pos.x, y: pos.y };
        if (n.data.label === "__summary__") base.classes = "summary-node";
        return base;
      }),
      ...renderData.edges.map((e) => ({
        group: "edges" as const,
        data: e.data,
        classes: "svg-overlay-edge",
      })),
      ...badgeNodes,
      ...badgeEdges,
    ];

    const cy = cytoscape({
      container: cytoMountRef.current,
      elements,
      style: cytoscapeStyles,
      layout: { name: "preset", fit: true, padding: 60 } as any,
      wheelSensitivity: 0.2,
      // Lock node positions: ELK already chose the optimal layout, and a
      // dragged node tears the SVG edge overlay free of its endpoints. The
      // user can still pan + zoom + tap; only node dragging is disabled.
      autoungrabify: true,
      // Marquee/box-select would also let users move groups — turn it off.
      boxSelectionEnabled: false,
    });
    cyRef.current = cy;

    if (centerNodeId) {
      const center = cy.getElementById(centerNodeId);
      if (!center.empty()) center.addClass("star-center");
    }

    // ---- Lineage highlight on tap --------------------------------------
    const realClosure = (node: any) =>
      node
        .predecessors()
        .union(node.successors())
        .union(node)
        .not(".expand-badge")
        .not(".expand-badge-edge");

    const applyHighlight = (node: any) => {
      const closure = realClosure(node);
      cy.elements().addClass("lin-off");
      closure.removeClass("lin-off").addClass("lin-on");
      node.removeClass("lin-on").addClass("lin-root");
      refreshSvgClasses(cy, svgRef.current);
    };

    const clearHighlight = () => {
      cy.elements().removeClass("lin-off lin-on lin-root");
      refreshSvgClasses(cy, svgRef.current);
    };

    cy.on("tap", "node", (evt) => {
      const n = evt.target;
      const id = n.id();
      const nodeData = n.data();

      // Tapping a summary chip expands its group on the next render.
      if (nodeData.label === "__summary__") {
        const parentId = String(nodeData._summary_parent_id ?? "");
        const memberLabel = String(nodeData._summary_member_label ?? "");
        if (parentId && memberLabel) {
          setExpandedSummaries((prev) => {
            const key = `${parentId}::${memberLabel}`;
            if (prev.has(key)) return prev;
            const next = new Set(prev);
            next.add(key);
            return next;
          });
        }
        // Also surface the member list in the sidebar so users can pick a
        // specific row to navigate to.
        if (onNodeTapRef.current) {
          onNodeTapRef.current(id, {
            id,
            data: nodeData,
            columns: undefined,
          });
        }
        return;
      }

      applyHighlight(n);
      if (onNodeTapRef.current) {
        const cols = columnsByOwnerRef.current.get(id);
        onNodeTapRef.current(id, { id, data: nodeData, columns: cols });
      }
    });
    cy.on("tap", (evt) => {
      if (evt.target === cy) clearHighlight();
    });

    // Sync overlay opacity whenever Cytoscape re-renders or the user pans /
    // zooms. ELK gave us static paths in graph-space; we project to screen
    // coords using cy.zoom() / cy.pan().
    const syncOverlay = () => {
      drawSvgOverlay(cy, layout, svgRef.current);
    };
    cy.on("render viewport pan zoom", syncOverlay);
    syncOverlay();

    const obs = new ResizeObserver(() => {
      cy.resize();
      cy.fit(undefined, 30);
    });
    if (containerRef.current) obs.observe(containerRef.current);

    return () => {
      obs.disconnect();
      cy.destroy();
      cyRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renderData, layout, centerNodeId]);

  // Imperative focus channel — bumping focusToken animates Cytoscape to the
  // node and pulses it briefly.
  useEffect(() => {
    if (!focusToken || !cyRef.current) return;
    const cy = cyRef.current;
    const node = cy.getElementById(focusToken.id);
    if (node.empty()) return;
    cy.animate(
      { center: { eles: node }, zoom: 1.2 },
      { duration: 400, easing: "ease-in-out" },
    );
    node.addClass("focused");
    const t = setTimeout(() => node.removeClass("focused"), 1500);
    return () => clearTimeout(t);
  }, [focusToken]);

  // -- Toolbar callbacks
  const handleSearch = (q: string) => {
    if (!cyRef.current) return;
    const cy = cyRef.current;
    const needle = q.toLowerCase();
    let hit: any = null;
    cy.nodes().some((node) => {
      const n = node as any;
      const props = (n.data("properties") as Record<string, any>) ?? {};
      const id = String(n.id() ?? "");
      const candidates = [
        id,
        props.name,
        props.fully_qualified_name,
        props.var_name,
        props.path,
        props.caption,
      ]
        .filter((s) => typeof s === "string")
        .map((s) => String(s).toLowerCase());
      if (candidates.some((c) => c.includes(needle))) {
        hit = n;
        return true;
      }
      return false;
    });
    if (hit) {
      cy.animate({ center: { eles: hit }, zoom: 1.2 }, { duration: 400 });
      hit.addClass("focused");
      setTimeout(() => hit.removeClass("focused"), 1500);
    }
  };
  const handleToggleType = (label: string) => {
    setVisibleTypes((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  };

  return (
    <div ref={containerRef} className="graph-canvas">
      <div ref={cytoMountRef} className="graph-canvas__cyto" />
      <svg
        ref={svgRef}
        className="graph-edge-overlay"
        xmlns="http://www.w3.org/2000/svg"
      />
      <GraphToolbar
        visibleTypes={visibleTypes}
        availableTypes={allTypes}
        onToggleType={handleToggleType}
        onZoomIn={() => cyRef.current?.zoom(cyRef.current.zoom() * 1.2)}
        onZoomOut={() => cyRef.current?.zoom(cyRef.current.zoom() / 1.2)}
        onFit={() => cyRef.current?.fit(undefined, 40)}
        onSearch={handleSearch}
      />
      <GraphMinimap cy={cyRef.current} />
    </div>
  );
}

/**
 * Project ELK's static graph-space SVG paths into screen space and write
 * them into the overlay <svg>. Called whenever Cytoscape re-renders so the
 * overlay stays glued to the node positions during pan/zoom.
 */
function drawSvgOverlay(
  cy: Core,
  layout: ElkLayoutResult,
  svg: SVGSVGElement | null,
) {
  if (!svg) return;
  const container = cy.container();
  if (!container) return;
  const rect = container.getBoundingClientRect();
  svg.setAttribute("width", `${rect.width}`);
  svg.setAttribute("height", `${rect.height}`);
  svg.setAttribute("viewBox", `0 0 ${rect.width} ${rect.height}`);

  const zoom = cy.zoom();
  const pan = cy.pan();
  // Compose a single transform on the root <g> so each individual path stays
  // a cheap d= string. We let CSS handle classes for highlight/dim.
  let root = svg.querySelector<SVGGElement>("g[data-root='1']");
  if (!root) {
    root = document.createElementNS("http://www.w3.org/2000/svg", "g");
    root.setAttribute("data-root", "1");
    svg.appendChild(root);
  }
  root.setAttribute("transform", `translate(${pan.x},${pan.y}) scale(${zoom})`);

  // Build/update <path> children keyed by edge id. We avoid React for the
  // inner SVG because we want sub-frame updates on pan/zoom.
  const pathsToKeep = new Set<string>();
  for (const [edgeId, d] of layout.edgePaths) {
    pathsToKeep.add(edgeId);
    let path = root.querySelector<SVGPathElement>(
      `path[data-edge="${cssEscape(edgeId)}"]`,
    );
    if (!path) {
      path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("data-edge", edgeId);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke-linejoin", "round");
      path.setAttribute("stroke-linecap", "round");
      path.setAttribute("marker-end", "url(#arrow)");
      root.appendChild(path);
    }
    path.setAttribute("d", d);
    const edgeData = cy.getElementById(edgeId);
    const label = String(edgeData?.data("label") ?? "");
    path.setAttribute("stroke", edgeColor(label));
    path.setAttribute("stroke-width", String(1.5 / zoom));
  }

  // Remove paths for edges no longer present.
  root.querySelectorAll<SVGPathElement>("path[data-edge]").forEach((p) => {
    const id = p.getAttribute("data-edge") ?? "";
    if (!pathsToKeep.has(id)) p.remove();
  });

  // Ensure the arrow marker exists once.
  ensureArrowMarker(svg);

  refreshSvgClasses(cy, svg);
}

function refreshSvgClasses(cy: Core, svg: SVGSVGElement | null) {
  if (!svg) return;
  svg.querySelectorAll<SVGPathElement>("path[data-edge]").forEach((p) => {
    const id = p.getAttribute("data-edge") ?? "";
    const edge = cy.getElementById(id);
    if (!edge || edge.empty()) return;
    const on = edge.hasClass("lin-on");
    const off = edge.hasClass("lin-off");
    p.classList.toggle("svg-edge--on", on);
    p.classList.toggle("svg-edge--off", off);
  });
}

function edgeColor(label: string): string {
  switch (label) {
    case "DERIVES_FROM":
      return "#0f62fe";
    case "READS_TABLE":
    case "READS":
    case "READS_CONNECTION":
      return "#198038";
    case "WRITES_TABLE":
    case "WRITES":
    case "WRITES_TO_CONNECTION":
      return "#da1e28";
    default:
      return "#8d8d8d";
  }
}

function ensureArrowMarker(svg: SVGSVGElement) {
  let defs = svg.querySelector("defs");
  if (!defs) {
    defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    svg.insertBefore(defs, svg.firstChild);
  }
  if (!defs.querySelector("#arrow")) {
    const marker = document.createElementNS(
      "http://www.w3.org/2000/svg",
      "marker",
    );
    marker.setAttribute("id", "arrow");
    marker.setAttribute("viewBox", "0 0 10 10");
    marker.setAttribute("refX", "9");
    marker.setAttribute("refY", "5");
    marker.setAttribute("markerWidth", "6");
    marker.setAttribute("markerHeight", "6");
    marker.setAttribute("orient", "auto-start-reverse");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
    path.setAttribute("fill", "context-stroke");
    marker.appendChild(path);
    defs.appendChild(marker);
  }
}

// CSS.escape isn't universal in older Safari; provide a safe fallback for
// the simple id forms we generate (alphanumerics + ::, ->, __, etc.).
function cssEscape(s: string): string {
  if (typeof CSS !== "undefined" && CSS.escape) return CSS.escape(s);
  return s.replace(/(["\\'\\\\.:>])/g, "\\$1");
}
