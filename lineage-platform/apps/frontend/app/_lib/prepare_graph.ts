/**
 * Pre-render data preparation for the graph canvas.
 *
 * The gateway returns the full subgraph including every Attribute node and
 * every ``HAS_COLUMN`` edge. With 80+ attributes hanging off the DataFrames,
 * the canvas drowns in detail. The fix from
 * ``spark-improvement/graph_layout_fix_plan.md`` §2 is to drop attributes
 * from the default render and surface them as a count chip on each
 * owning DataFrame / Table — clicking the owner opens the column list in
 * the existing sidebar.
 *
 * ``prepareGraphData`` returns:
 *   - ``visibleData``: the graph minus attributes/HAS_COLUMN edges, with
 *     every Table/DataFrame node carrying a ``_col_count`` integer.
 *   - ``columnsByOwnerId``: the full column list keyed by owner node id.
 *
 * The toolbar's "Attribute" type filter can re-include attributes by
 * passing ``{includeAttributes: true}``.
 */
import type { GraphEdge, GraphNode, GraphPayload } from "./api";
import { computeNodeSize } from "./cytoscape-config";

export interface ColumnMeta {
  id: string;
  name: string;
  datatype?: string;
  is_calculated?: boolean;
  is_derived?: boolean;
}

export interface PreparedGraph {
  visibleData: GraphPayload;
  columnsByOwnerId: Map<string, ColumnMeta[]>;
}

export interface PrepareOptions {
  includeAttributes?: boolean;
  typeFilter?: Set<string> | null;
}

// Node ``label`` values we consider "an attribute" (Attribute is the spark
// + tableau convention; QlikView fields land under the same Attribute label).
const ATTRIBUTE_LABELS = new Set(["Attribute", "Column", "Field"]);

// Edges that link an owner to its columns. Filtered out by default.
const COLUMN_EDGE_LABELS = new Set(["HAS_COLUMN", "HAS_FIELD"]);

// Owners that carry columns. Spark = DataFrame, others = Table.
const OWNER_LABELS = new Set(["Table", "DataFrame"]);

export function prepareGraphData(
  data: GraphPayload,
  opts: PrepareOptions = {},
): PreparedGraph {
  const { includeAttributes = false, typeFilter = null } = opts;

  const nodeById = new Map<string, GraphNode>();
  data.nodes.forEach((n) => nodeById.set(n.data.id, n));

  const columnsByOwnerId = new Map<string, ColumnMeta[]>();

  for (const e of data.edges) {
    if (!COLUMN_EDGE_LABELS.has(e.data.label)) continue;
    const owner = nodeById.get(e.data.source);
    const attr = nodeById.get(e.data.target);
    if (!owner || !attr) continue;
    if (!OWNER_LABELS.has(owner.data.label)) continue;
    const p = attr.data.properties ?? {};
    const meta: ColumnMeta = {
      id: attr.data.id,
      name: String(p.name ?? attr.data.id),
      datatype: (p.datatype as string | undefined) ?? undefined,
      is_calculated: Boolean(p.is_calculated as boolean),
      is_derived: Boolean(p.is_derived as boolean),
    };
    const list = columnsByOwnerId.get(owner.data.id) ?? [];
    list.push(meta);
    columnsByOwnerId.set(owner.data.id, list);
  }

  // Drop attribute nodes + column edges by default. Stamp _col_count on
  // owners so the canvas can render the "83 cols" chip without re-walking.
  const passesTypeFilter = (label: string): boolean => {
    if (!typeFilter) return true;
    return typeFilter.has(label);
  };

  const visibleNodes: GraphNode[] = [];
  for (const n of data.nodes) {
    const label = n.data.label;
    if (!includeAttributes && ATTRIBUTE_LABELS.has(label)) continue;
    if (!passesTypeFilter(label)) continue;
    const cols = columnsByOwnerId.get(n.data.id);
    const enriched: any =
      cols && cols.length > 0
        ? { ...n.data, _col_count: cols.length }
        : { ...n.data };
    // Pre-compute the exact box size from the same line layout Cytoscape
    // will render. ELK reserves that footprint during layered placement so
    // neighbouring boxes never overlap, even when one of them stretches to
    // accommodate extra extras (datatype + calculated + role + formula).
    enriched._size = computeNodeSize(enriched);
    visibleNodes.push({ ...n, data: enriched as GraphNode["data"] });
  }

  const visibleIds = new Set(visibleNodes.map((n) => n.data.id));
  const visibleEdges: GraphEdge[] = data.edges.filter((e) => {
    if (!includeAttributes && COLUMN_EDGE_LABELS.has(e.data.label)) return false;
    return visibleIds.has(e.data.source) && visibleIds.has(e.data.target);
  });

  return {
    visibleData: { nodes: visibleNodes, edges: visibleEdges, rows: data.rows },
    columnsByOwnerId,
  };
}
