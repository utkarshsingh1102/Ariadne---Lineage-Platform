/**
 * Pre-layout fan-out cap (plan §1 Addendum A).
 *
 * ELK will dutifully lay out 80 same-type children side by side and produce
 * a 6000-px-wide tier. Cap it before the renderer ever sees it: any node
 * with more than ``MAX_VISIBLE_CHILDREN`` children of the SAME type is
 * collapsed into one summary node carrying the hidden member ids.
 *
 * The summary node is just a regular graph node with ``_summary_*`` data
 * fields so existing tap-to-expand / sidebar code can recognise it without
 * a separate render path.
 */
import type { GraphEdge, GraphNode, GraphPayload } from "./api";

export const MAX_VISIBLE_CHILDREN = 12;

export interface FanoutCapOptions {
  max?: number;
  /** Set of parent ids whose summary nodes the user has expanded. */
  expanded?: Set<string>;
}

export interface CappedGraph {
  data: GraphPayload;
  /** Map summary-node-id → array of member node ids that were hidden behind it. */
  hiddenBySummary: Map<string, string[]>;
}

export function capFanout(
  data: GraphPayload,
  opts: FanoutCapOptions = {},
): CappedGraph {
  const { max = MAX_VISIBLE_CHILDREN, expanded = new Set<string>() } = opts;

  const nodeById = new Map<string, GraphNode>();
  data.nodes.forEach((n) => nodeById.set(n.data.id, n));

  // For each (parent, child-type) we collect the outgoing edges. The "parent"
  // is whichever endpoint of the edge has fewer outgoing edges of this type;
  // in practice for lineage we treat the source endpoint as the parent. We
  // group by source so a single source with 80 sinks of the same type gets
  // capped, mirroring the visual problem.
  const groups = new Map<string, Map<string, string[]>>(); // sourceId → label → childIds
  for (const e of data.edges) {
    const tgt = nodeById.get(e.data.target);
    if (!tgt) continue;
    const label = tgt.data.label;
    const bySource = groups.get(e.data.source) ?? new Map<string, string[]>();
    const list = bySource.get(label) ?? [];
    list.push(e.data.target);
    bySource.set(label, list);
    groups.set(e.data.source, bySource);
  }

  const hiddenBySummary = new Map<string, string[]>();
  const childToSummary = new Map<string, string>(); // hiddenChildId → summaryNodeId
  // We only collapse a child if it is hidden behind EXACTLY ONE summary —
  // children with multiple parents stay visible. Track "claims" first, then
  // commit only the children whose every incoming edge agrees on the same
  // summary.
  const claims = new Map<string, Set<string>>(); // childId → set of summary ids claiming it

  for (const [sourceId, byLabel] of groups) {
    for (const [label, childIds] of byLabel) {
      if (childIds.length <= max) continue;
      if (expanded.has(`${sourceId}::${label}`)) continue;
      const keepCount = Math.max(0, max - 1); // reserve one slot for the summary chip
      const keep = new Set(childIds.slice(0, keepCount));
      const hidden = childIds.filter((id) => !keep.has(id));
      if (hidden.length === 0) continue;
      const summaryId = `__summary__${sourceId}__${label}`;
      hiddenBySummary.set(summaryId, hidden);
      for (const cid of hidden) {
        const set = claims.get(cid) ?? new Set<string>();
        set.add(summaryId);
        claims.set(cid, set);
      }
    }
  }

  for (const [childId, summaryIds] of claims) {
    if (summaryIds.size === 1) {
      childToSummary.set(childId, [...summaryIds][0]);
    }
  }

  // Build the capped graph: keep originals minus hidden children, plus one
  // summary node per group, plus rerouted edges.
  const hiddenSet = new Set(childToSummary.keys());
  const nodesOut: GraphNode[] = data.nodes.filter(
    (n) => !hiddenSet.has(n.data.id),
  );

  // Summary nodes inherit the parent's source_system (one of "spark",
  // "tableau", etc.) so the colour ramp stays consistent.
  for (const [summaryId, members] of hiddenBySummary) {
    const parts = summaryId.replace(/^__summary__/, "").split("__");
    const sourceId = parts.slice(0, -1).join("__");
    const label = parts[parts.length - 1];
    const parent = nodeById.get(sourceId);
    // If the parent is itself hidden behind another summary, skip — this
    // would create dangling summaries.
    if (parent && hiddenSet.has(parent.data.id)) continue;
    nodesOut.push({
      data: {
        id: summaryId,
        label: "__summary__",
        source_system: parent?.data.source_system,
        _summary_label: `+${members.length} ${label}${members.length === 1 ? "" : "s"}`,
        _summary_count: members.length,
        _summary_parent_id: sourceId,
        _summary_member_label: label,
        _summary_member_ids: members,
        // Reserve the same footprint Cytoscape will paint so ELK doesn't
        // overlap the summary chip with siblings.
        _size: { width: 180, height: 56 },
      } as GraphNode["data"],
    });
  }

  // Edges: rewrite endpoints that point at hidden children to point at the
  // summary node, then dedupe. CRITICAL: when a summary was skipped because
  // its parent is itself hidden behind another summary, the rewrite would
  // point at a non-existent node. Filter against ``nodeIdsOut`` so those
  // edges drop cleanly instead of crashing Cytoscape with "nonexistent
  // target".
  const nodeIdsOut = new Set(nodesOut.map((n) => n.data.id));
  const edgesOut: GraphEdge[] = [];
  const seenEdgeIds = new Set<string>();
  for (const e of data.edges) {
    const newSource = childToSummary.get(e.data.source) ?? e.data.source;
    const newTarget = childToSummary.get(e.data.target) ?? e.data.target;
    // Drop edges that collapse to a self-loop on the summary.
    if (newSource === newTarget) continue;
    // Drop edges whose rewritten endpoint refers to a summary that we chose
    // NOT to create (its parent is hidden behind another summary).
    if (!nodeIdsOut.has(newSource) || !nodeIdsOut.has(newTarget)) continue;
    const newId =
      newSource !== e.data.source || newTarget !== e.data.target
        ? `${e.data.id}::${newSource}->${newTarget}`
        : e.data.id;
    if (seenEdgeIds.has(newId)) continue;
    seenEdgeIds.add(newId);
    edgesOut.push({
      data: {
        ...e.data,
        id: newId,
        source: newSource,
        target: newTarget,
      },
    });
  }

  return {
    data: { nodes: nodesOut, edges: edgesOut, rows: data.rows },
    hiddenBySummary,
  };
}
