/**
 * Cytoscape style + layout.
 *
 * Nodes render as rounded rectangles ("cards") that show the type, the node's
 * actual name (workbook name, table name, attribute name, …) and a small set
 * of per-type properties. The full property bag is still surfaced via the
 * detail Modal when the user clicks a node.
 *
 * Node tint = source_system. Edge stroke = relationship category.
 */
import type { LayoutOptions } from "cytoscape";

type Stylesheet = { selector: string; style: Record<string, unknown> };

// ---------------------------------------------------------------------------
// Colours — full-saturation border for each source system
// ---------------------------------------------------------------------------
export const SOURCE_SYSTEM_COLORS: Record<string, string> = {
  tableau: "#0f62fe", // Carbon Blue 60
  qlikview: "#198038", // Carbon Green 60
  tws: "#d02670", // Carbon Magenta 60
  spark: "#d2a106", // Carbon Yellow 50
  shared: "#8a3ffc", // Carbon Purple 60
  unknown: "#6f6f6f", // Carbon Gray 60
};

// Lighter tints used as the card backgrounds so the text inside reads clearly
// on a Carbon White surface.
export const SOURCE_SYSTEM_BG: Record<string, string> = {
  tableau: "#edf5ff", // Blue 10
  qlikview: "#defbe6", // Green 10
  tws: "#fff0f7", // Magenta 10
  spark: "#fcf4d6", // Yellow 10
  shared: "#f6f2ff", // Purple 10
  unknown: "#f4f4f4", // Gray 10
};

export const LINEAGE_EDGE_TYPES = new Set([
  "READS_TABLE",
  "WRITES_TABLE",
  "DERIVES_FROM",
  "HAS_FIELD",
  "USES_FIELD",
  "HAS_COLUMN",
  "CALLS_SCRIPT",
  // TWS v0.2 — script + topology edges count as lineage for traversal.
  "EXECUTES",
  "DEPENDS_ON",
  "REQUIRES_RESOURCE",
  "WAITS_FOR_FILE",
  "WAITS_FOR_PROMPT",
  "RUNS_ON",
  "HOSTS_STREAM",
  "RECOVERS_WITH",
  "TRIGGERS",
  "SCHEDULED_BY",
  // QlikView v0.2 / Phase 3 — richer attribute-level edges.
  "CONNECTS_VIA",
  "SOURCED_FROM",
  "HAS_ATTRIBUTE",
  "STORED_AS",
  "MAPS_TO",
  "JOINS",
  "REFERENCES_FK",
  "HAS_CONSTRAINT",
  "FEEDS_OBJECT",
]);

// ---------------------------------------------------------------------------
// Label builder — picks the user-meaningful name and adds per-type context.
// ---------------------------------------------------------------------------

const NAME_FALLBACK_KEYS = [
  "name",
  "fully_qualified_name",
  "path",
  "var_name",
  "caption",
  "title",
];

function pickDisplayName(props: Record<string, any>, id: string): string {
  for (const k of NAME_FALLBACK_KEYS) {
    const v = props[k];
    if (typeof v === "string" && v.trim()) return v;
  }
  return id;
}

function truncate(s: string, max = 28): string {
  if (s.length <= max) return s;
  return s.slice(0, max - 1) + "…";
}

function extrasFor(type: string, props: Record<string, any>): string[] {
  const lines: string[] = [];
  const add = (k: string, v: unknown) => {
    if (v === undefined || v === null || v === "") return;
    lines.push(`${k}: ${truncate(String(v))}`);
  };

  switch (type) {
    case "Table":
      add("database", props.database);
      add("schema", props.schema);
      add("format", props.storage_format);
      break;
    case "Attribute":
      add("datatype", props.datatype);
      if (props.is_calculated) lines.push("calculated");
      add("role", props.role);
      if (props.formula) lines.push(`= ${truncate(String(props.formula), 24)}`);
      break;
    case "Connection":
      add("class", props.class);
      add("server", props.server);
      add("dbname", props.dbname);
      break;
    case "TableauWorkbook":
      add("version", props.version);
      break;
    case "TableauDatasource":
      if (props.has_extract) lines.push("extract");
      if (props.is_federated) lines.push("federated");
      break;
    case "TableauWorksheet":
    case "TableauDashboard":
      // name is enough
      break;
    case "DashboardZone":
      // Non-worksheet dashboard slots (filter / parameter / text /
      // image / web / container). Surface the kind so the visual is
      // self-explanatory without opening the side panel.
      add("kind", props.kind);
      if (props.target_parameter) add("controls", props.target_parameter);
      break;
    case "TableauGroup":
    case "TableauSet":
    case "TableauBin":
    case "TableauHierarchy":
      // Derived-field families (plan §6). Name + datasource_id is
      // sufficient; the actual source-field membership is encoded as
      // outgoing DERIVES_FROM edges in the graph.
      if (props.size) add("size", props.size);
      if (props.condition_expr) add("when", props.condition_expr);
      break;
    case "TableauParameterScope":
      // Improvement-v2 §4 — synthetic node for the Parameters block.
      // Owns Parameter nodes via HAS_PARAMETER. Name is always
      // "Parameters" but we surface it for clarity in mixed graphs.
      add("scope", props.name || "Parameters");
      break;
    case "WorksheetBlend":
      // Improvement-v2 §9 — one per <datasource-relationship>. Show the
      // linked datasources and the join field(s) at-a-glance.
      if (props.primary_datasource_name) {
        add("primary", props.primary_datasource_name);
      }
      if (props.secondary_datasource_name) {
        add("secondary", props.secondary_datasource_name);
      }
      if (Array.isArray(props.on_field_names) && props.on_field_names.length) {
        add("on", props.on_field_names.join(", "));
      }
      break;
    case "Parameter":
      add("datatype", props.datatype);
      break;
    case "QlikScript":
    case "QlikTable":
    case "QlikSheet":
    case "QlikChart":
      add("chart_type", props.chart_type);
      break;
    // QlikView v0.2 / Phase 3 — richer node labels.
    case "DataPlatform":
      add("kind", props.kind);
      add("cloud", props.vendor_cloud);
      add("locator", props.account_locator);
      break;
    case "DataConnection":
      add("platform", props.platform_kind);
      add("host", props.host);
      add("database", props.database);
      add("warehouse", props.warehouse);
      add("role", props.role);
      add("auth", props.auth_method);
      break;
    case "PhysicalSource":
      add("kind", props.kind);
      add("locator", props.locator);
      break;
    case "Dataset":
      add("origin", props.origin);
      if (props.is_synthetic_key_table) lines.push("$Syn key");
      if (props.is_mapping_table) lines.push("mapping");
      break;
    case "KeyConstraint":
      add("kind", props.kind);
      add("source", props.source);
      if (Array.isArray(props.columns)) add("cols", props.columns.join("+"));
      if (typeof props.confidence === "number") {
        add("conf", props.confidence.toFixed(2));
      }
      break;
    case "UiObject":
      add("qtype", props.qtype);
      add("title", props.qtitle);
      break;
    case "ServerTask":
      add("type", props.task_type);
      add("app", props.app_path);
      if (props.enabled === false) lines.push("disabled");
      break;
    case "Trigger":
      add("kind", props.kind);
      add("schedule", props.schedule);
      break;
    case "Variable":
      add("scope", props.scope);
      break;
    case "Subroutine":
      // name is enough
      break;
    case "Schedule":
      add("workstation", props.workstation);
      add("scheduler", props.scheduler);
      break;
    case "Job":
      add("workstation", props.workstation);
      add("stream", props.stream);
      add("start", props.start_time);
      add("priority", props.priority);
      if (props.recovery) add("recovery", props.recovery);
      break;
    case "Resource":
      add("quantity", props.quantity);
      break;
    case "FileWatcher":
      add("path", props.path);
      break;
    // TWS v0.2 — new topology node labels.
    case "Workstation":
      add("os", props.os);
      add("type", props.type);
      add("node", props.node);
      if (props.tcp_addr) add("tcp", props.tcp_addr);
      break;
    case "JobStream":
      add("workstation", props.workstation);
      add("start", props.start_time);
      if (props.deadline) add("deadline", props.deadline);
      add("priority", props.priority);
      if (props.limit) add("limit", props.limit);
      break;
    case "Calendar":
      if (Array.isArray(props.dates)) add("dates", `${props.dates.length}`);
      break;
    case "Prompt":
      if (props.text) add("text", props.text);
      break;
    case "EventRule":
      if (props.active) lines.push("active");
      add("event", props.event_type);
      if (props.event_filename) add("file", props.event_filename);
      add("action", props.action_type);
      break;
    case "Script":
      add("type", props.script_type);
      break;
    case "SparkScript":
      add("type", props.script_type);
      break;
    case "DataFrame":
      add("order", props.creation_order);
      if (props.is_anonymous) lines.push("anonymous");
      break;
    case "UDF":
      add("returns", props.return_type);
      if (props.is_pandas_udf) lines.push("pandas_udf");
      break;
  }
  return lines;
}

const MAX_COLS_SHOWN = 8;
const COL_NAME_PAD = 18;

function padRight(s: string, n: number): string {
  if (s.length >= n) return s;
  return s + " ".repeat(n - s.length);
}

function tableColumnLines(
  cols: Array<{ name: string; datatype?: string; is_calculated?: boolean }>,
): string[] {
  const out: string[] = ["", `Columns (${cols.length})`, "─".repeat(22)];
  const visible = cols.slice(0, MAX_COLS_SHOWN);
  for (const c of visible) {
    const star = c.is_calculated ? " *" : "";
    const t = c.datatype ? c.datatype : "";
    out.push(`${padRight(truncate(c.name, COL_NAME_PAD), COL_NAME_PAD)}${t}${star}`);
  }
  if (cols.length > MAX_COLS_SHOWN) {
    out.push(`+ ${cols.length - MAX_COLS_SHOWN} more`);
  }
  return out;
}

/**
 * Compute the exact text lines for a node. Single source of truth — every
 * downstream concern (label text, height, width, ELK input sizing) reads
 * from here so the rendered box always exactly fits its content.
 */
export function nodeLines(data: any): string[] {
  if (data?.label === "__summary__") {
    return [String(data._summary_label ?? "+more")];
  }
  const props: Record<string, any> = data?.properties ?? {};
  const type: string = data?.label ?? "Node";
  const name = pickDisplayName(props, String(data?.id ?? ""));
  const lines: string[] = [type, truncate(name, 30)];
  const extras = extrasFor(type, props);
  if (extras.length) lines.push("", ...extras);
  if (typeof data?._col_count === "number" && data._col_count > 0) {
    lines.push("", `${data._col_count} cols`);
  }
  return lines;
}

function buildNodeLabel(ele: cytoscape.NodeSingular): string {
  return nodeLines(ele.data()).join("\n");
}

// Leaf-ish labels (Attribute, UDF, Connection) don't need the full card
// real-estate. Shrinking them lets the LR layout pack many siblings
// horizontally without inflating the canvas vertically.
const COMPACT_LABELS = new Set([
  "Attribute",
  "UDF",
  "Connection",
  "Parameter",
  "Variable",
  "Resource",
  "FileWatcher",
  // TWS v0.2 — leaf-ish nodes.
  "Calendar",
  "Prompt",
]);

// IBM Plex Sans @ 10px ≈ 5.8-6.2px per char. Round up a bit so the longest
// line never just kisses the border. ``LINE_HEIGHT_PX`` follows the
// font-size × line-height (10 * 1.25 = 12.5), rounded up so descenders
// don't get clipped at the bottom edge.
const LINE_HEIGHT_PX = 14;
const CHAR_WIDTH_PX = 6.4;
const VERT_PADDING_PX = 16;
const HORIZ_PADDING_PX = 28;

/**
 * Compute the box dimensions that exactly contain ``nodeLines(data)`` —
 * no overflow, no excess whitespace. Used as the single source of truth
 * for both Cytoscape rendering and the ELK pre-layout pass so the laid-out
 * positions never collide with content that fell outside the reserved box.
 */
export function computeNodeSize(data: any): { width: number; height: number } {
  if (data?.label === "__summary__") return { width: 180, height: 56 };
  const lines = nodeLines(data);
  const lineCount = lines.length;
  const longestLen = lines.reduce((m, l) => Math.max(m, l.length), 0);
  const isCompact = COMPACT_LABELS.has(String(data?.label));
  const minWidth = isCompact ? 160 : 200;
  const maxWidth = 320;
  const width = Math.min(
    maxWidth,
    Math.max(minWidth, Math.ceil(longestLen * CHAR_WIDTH_PX + HORIZ_PADDING_PX)),
  );
  // Lift the minimum height for compact-but-multi-line nodes (e.g. an
  // Attribute with datatype + calculated + role + formula extras) so the
  // last line stays inside the rounded rectangle.
  const minHeight = isCompact && lineCount <= 2 ? 56 : 80;
  const height = Math.max(
    minHeight,
    Math.ceil(lineCount * LINE_HEIGHT_PX + VERT_PADDING_PX),
  );
  return { width, height };
}

function nodeHeight(ele: cytoscape.NodeSingular): number {
  const data: any = ele.data();
  if (typeof data?._size?.height === "number") return data._size.height;
  return computeNodeSize(data).height;
}

function nodeWidth(ele: cytoscape.NodeSingular): number {
  const data: any = ele.data();
  if (typeof data?._size?.width === "number") return data._size.width;
  return computeNodeSize(data).width;
}

// ---------------------------------------------------------------------------
// Stylesheet
// ---------------------------------------------------------------------------
export const cytoscapeStyles: Stylesheet[] = [
  {
    selector: "node",
    style: {
      shape: "round-rectangle",
      width: nodeWidth,
      height: nodeHeight,
      "background-color": (ele: cytoscape.NodeSingular) =>
        SOURCE_SYSTEM_BG[
          (ele.data("source_system") as string) || "unknown"
        ] ?? SOURCE_SYSTEM_BG.unknown,
      "background-opacity": 1,
      "border-width": 2,
      "border-color": (ele: cytoscape.NodeSingular) =>
        SOURCE_SYSTEM_COLORS[
          (ele.data("source_system") as string) || "unknown"
        ] ?? SOURCE_SYSTEM_COLORS.unknown,
      label: buildNodeLabel,
      color: "#161616",
      "font-size": 10,
      "font-family": "IBM Plex Sans, IBM Plex Mono, sans-serif",
      "text-valign": "center",
      "text-halign": "center",
      "text-wrap": "wrap",
      // Cap wrap to the box's actual width minus padding so a long line
      // never bleeds outside the rounded border. Sized dynamically per
      // node via ``computeNodeSize``.
      "text-max-width": (ele: cytoscape.NodeSingular) =>
        `${Math.max(0, nodeWidth(ele) - HORIZ_PADDING_PX)}px`,
      "line-height": 1.25,
      "text-justification": "center",
      "padding-top": "6px",
      "padding-bottom": "6px",
      "padding-left": "6px",
      "padding-right": "6px",
    } as any,
  },
  {
    selector: "node:selected",
    style: {
      "border-width": 4,
      "border-color": "#0f62fe",
      "background-color": "#ffffff",
    },
  },
  {
    // Expansion badge — small circle hugged next to a node that has hidden
    // neighbours. Shows the count; tapping expands the parent.
    selector: "node.expand-badge",
    style: {
      shape: "ellipse",
      width: 40,
      height: 40,
      "background-color": "#0f62fe",
      "background-opacity": 1,
      "border-width": 2,
      "border-color": "#ffffff",
      label: "data(_badge_label)",
      color: "#ffffff",
      "font-size": 12,
      "font-weight": 600,
      "text-valign": "center",
      "text-halign": "center",
      "z-index": 40,
    } as any,
  },
  {
    selector: "edge.expand-badge-edge",
    style: {
      width: 1,
      "line-color": "#a6c8ff",
      "target-arrow-shape": "none",
      "source-arrow-shape": "none",
      label: "",
      "curve-style": "straight",
    } as any,
  },
  {
    // Summary node — slate-grey card with a centred "+N more" label.
    // Source-system tint is preserved as the background; the heavier border
    // and dashed style signal that this is a placeholder for hidden siblings.
    selector: "node.summary-node",
    style: {
      shape: "round-rectangle",
      "border-style": "dashed",
      "border-width": 2,
      "border-color": "#6f6f6f",
      "background-color": "#f4f4f4",
      "background-opacity": 1,
      label: "data(_summary_label)",
      "font-weight": 600,
      "font-size": 11,
      color: "#161616",
      "text-valign": "center",
      "text-halign": "center",
      "text-wrap": "wrap",
    } as any,
  },
  {
    // The SVG overlay draws every NON-badge edge, so hide Cytoscape's own
    // bezier for those edges. Keep badge edges visible (Cytoscape handles
    // them natively because they don't exist in the ELK graph).
    selector: "edge.svg-overlay-edge",
    style: {
      opacity: 0,
      "text-opacity": 0,
      "target-arrow-shape": "none",
      "source-arrow-shape": "none",
    } as any,
  },
  {
    // Whenever a lineage highlight is active, hide expansion badges
    // entirely — they're navigation chrome, not part of the lineage.
    selector:
      "node.expand-badge.lin-off, edge.expand-badge-edge.lin-off",
    style: {
      display: "none",
    } as any,
  },
  {
    // Star-layout center node — keep the source-system fill so the legend
    // stays accurate; mark the focus with a thicker outer ring instead.
    selector: "node.star-center",
    style: {
      "border-width": 5,
      "border-style": "double",
      "z-index": 30,
    } as any,
  },
  {
    // Transient class added by GraphCanvas.focusToken — pulses the node
    // briefly so it's easy to spot after centering.
    selector: "node.focused",
    style: {
      "border-width": 5,
      "border-color": "#fa4d56",
      "border-opacity": 1,
    },
  },
  {
    // PERSISTENT class added by the toolbar's cumulative search. Distinct
    // colour from .focused (orange amber vs red) so the user can tell
    // "I'm parked on this node" from "this node matched a saved search".
    // Stays applied until the user removes the search term.
    selector: "node.highlighted",
    style: {
      "border-width": 4,
      "border-color": "#f1c21b",
      "border-opacity": 1,
      "border-style": "solid",
      "z-index": 25,
    } as any,
  },
  // -- Lineage highlight on tap -------------------------------------------
  // Elements in the upstream+downstream closure of the tapped node get
  // .lin-on; everything else gets .lin-off (heavily faded so the active
  // path stands out).
  {
    selector: "node.lin-off",
    style: {
      opacity: 0.12,
      "background-opacity": 0.2,
      "border-opacity": 0.2,
      "text-opacity": 0.3,
    } as any,
  },
  {
    selector: "edge.lin-off",
    style: {
      opacity: 0.08,
      "text-opacity": 0,
    } as any,
  },
  {
    selector: "node.lin-on",
    style: {
      "border-width": 3,
      "z-index": 10,
    } as any,
  },
  {
    selector: "node.lin-root",
    style: {
      "border-width": 4,
      "border-color": "#0f62fe",
      "background-color": "#ffffff",
      "z-index": 20,
    } as any,
  },
  {
    selector: "edge.lin-on",
    style: {
      width: 3,
      "line-color": "#0f62fe",
      "target-arrow-color": "#0f62fe",
      "z-index": 5,
    } as any,
  },
  {
    selector: "edge",
    style: {
      width: 1.5,
      "curve-style": "bezier",
      "line-color": "#8d8d8d",
      "target-arrow-color": "#8d8d8d",
      "target-arrow-shape": "triangle",
      "arrow-scale": 1,
      "font-size": 9,
      "font-family": "IBM Plex Sans, sans-serif",
      color: "#525252",
      label: "data(label)",
      "text-rotation": "autorotate" as any,
      "text-background-color": "#ffffff",
      "text-background-opacity": 0.95,
      "text-background-padding": "3px",
      "text-border-opacity": 0.6,
      "text-border-width": 1,
      "text-border-color": "#e0e0e0",
    } as any,
  },
  {
    selector: "edge[label = 'DERIVES_FROM']",
    style: { "line-color": "#0f62fe", "target-arrow-color": "#0f62fe" },
  },
  {
    selector: "edge[label = 'READS_TABLE']",
    style: { "line-color": "#198038", "target-arrow-color": "#198038" },
  },
  {
    selector: "edge[label = 'WRITES_TABLE']",
    style: { "line-color": "#da1e28", "target-arrow-color": "#da1e28" },
  },
  // TWS v0.2 — distinct stroke for the topology edges so they read at a
  // glance against shared backbone edges (CONTAINS_*, HAS_*). Magenta is
  // the TWS source-system tint per SOURCE_SYSTEM_COLORS.
  {
    selector: "edge[label = 'DEPENDS_ON']",
    style: { "line-color": "#d02670", "target-arrow-color": "#d02670" },
  },
  {
    selector: "edge[label = 'RECOVERS_WITH']",
    // Recovery paths get a dashed red treatment — "this edge fires on failure".
    style: {
      "line-color": "#da1e28",
      "target-arrow-color": "#da1e28",
      "line-style": "dashed",
    } as any,
  },
  {
    selector: "edge[label = 'TRIGGERS']",
    // Event triggers get a darker amber — distinguishes scheduler-driven
    // edges (TRIGGERS) from cron-driven edges (SCHEDULED_BY).
    style: { "line-color": "#b28600", "target-arrow-color": "#b28600" },
  },
  {
    selector: "edge[label = 'EXECUTES']",
    style: { "line-color": "#525252", "target-arrow-color": "#525252" },
  },
  // QlikView v0.2 / Phase 3 — distinct strokes for the new edge vocab.
  {
    selector: "edge[label = 'CONNECTS_VIA']",
    style: { "line-color": "#198038", "target-arrow-color": "#198038" },
  },
  {
    selector: "edge[label = 'SOURCED_FROM']",
    style: { "line-color": "#0e6027", "target-arrow-color": "#0e6027" },
  },
  {
    selector: "edge[label = 'HAS_ATTRIBUTE']",
    style: { "line-color": "#a7c4a3", "target-arrow-color": "#a7c4a3" },
  },
  {
    selector: "edge[label = 'STORED_AS']",
    style: {
      "line-color": "#8a3ffc",
      "target-arrow-color": "#8a3ffc",
      "line-style": "dashed",
    } as any,
  },
  {
    selector: "edge[label = 'MAPS_TO']",
    style: { "line-color": "#d2a106", "target-arrow-color": "#d2a106" },
  },
  {
    selector: "edge[label = 'JOINS']",
    style: { "line-color": "#a56eff", "target-arrow-color": "#a56eff" },
  },
  {
    selector: "edge[label = 'REFERENCES_FK']",
    style: {
      "line-color": "#0f62fe",
      "target-arrow-color": "#0f62fe",
      "line-style": "dotted",
    } as any,
  },
  {
    selector: "edge[label = 'HAS_CONSTRAINT']",
    style: { "line-color": "#8d8d8d", "target-arrow-color": "#8d8d8d" },
  },
  {
    selector: "edge[label = 'FEEDS_OBJECT']",
    style: { "line-color": "#1192e8", "target-arrow-color": "#1192e8" },
  },
  {
    // CONTAINS_* / HAS_FIELD edges in star mode — drop the label, lighten
    // the line. With 30+ siblings sharing the same label these clutter
    // the canvas without adding signal. Lineage edges (READS_TABLE etc.)
    // keep their labels.
    selector: "edge.star-quiet",
    style: {
      label: "",
      "line-color": "#c6c6c6",
      "target-arrow-color": "#c6c6c6",
      width: 1,
      "target-arrow-shape": "tee",
    } as any,
  },
];

// ---------------------------------------------------------------------------
// Layouts — larger cards need more breathing room between nodes/ranks.
// ---------------------------------------------------------------------------
export const dagreLayout: LayoutOptions = {
  name: "dagre",
  rankDir: "LR",
  // Tight vertical packing — siblings within the same rank are dense
  // (an LR layout stacks them vertically). Horizontal generosity stays
  // because the eye follows the flow left → right.
  nodeSep: 20,
  rankSep: 160,
  edgeSep: 12,
  ranker: "tight-tree",
  padding: 30,
  fit: true,
} as any;

export const coseLayout: LayoutOptions = {
  name: "cose",
  animate: false,
  padding: 40,
  nodeRepulsion: () => 24000,
  idealEdgeLength: () => 180,
  fit: true,
} as any;

// Star/radial layout — the trace start node sits in the middle and its
// neighbours fan out around it. Each subsequent expansion creates a sub
// star around the expanded node.
export function starLayout(centerId: string): LayoutOptions {
  return {
    name: "breadthfirst",
    roots: [centerId],
    circle: true,
    directed: false,
    spacingFactor: 1.6,
    padding: 40,
    avoidOverlap: true,
    grid: false,
    fit: true,
    animate: false,
  } as any;
}
