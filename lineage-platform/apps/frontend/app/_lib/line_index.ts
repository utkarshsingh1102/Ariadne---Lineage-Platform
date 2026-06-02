/**
 * Two-way line ↔ node index for the source-code viewer.
 *
 * The spark parser already stamps line metadata on every emitted node:
 *   - :SparkScript ............ no line (whole file)
 *   - :DataFrame .............. line_start / line_end
 *   - transform_chain step .... line (per op)
 *   - :Table / :Connection .... line (call-site)
 *
 * We never re-parse or guess. The index reads those fields straight off
 * the GraphPayload and produces:
 *   - ``nodeRanges``: node-id → {start,end} (single line collapses to
 *     start==end). Used by the panel to scroll + highlight when a node is
 *     clicked in the graph.
 *   - ``lineToNodes``: 1-based line number → list of node ids that cover
 *     it. Used for reverse-linking when the user clicks a line.
 *
 * A node may contribute multiple ranges via its transform_chain steps;
 * we surface each step's line individually so a click on an inner line
 * still resolves to the right node. The chain JSON is stored as a string
 * property — parse defensively.
 */
import type { GraphPayload } from "./api";

export interface NodeRange {
  start: number;
  end: number;
  /** Nested ranges contributed by transform-chain steps within this
   *  node, useful when the user clicks an inner step in the sidebar. */
  steps?: Array<{ seq: number; line: number; op: string }>;
}

export interface LineIndex {
  nodeRanges: Map<string, NodeRange>;
  lineToNodes: Map<number, string[]>;
}

function asNumber(v: unknown): number | undefined {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  }
  return undefined;
}

function asRange(v: unknown): [number, number] | undefined {
  if (!Array.isArray(v) || v.length !== 2) return undefined;
  const s = asNumber(v[0]);
  const e = asNumber(v[1]);
  if (s === undefined || e === undefined) return undefined;
  return [Math.min(s, e), Math.max(s, e)];
}

function rangeFromProps(p: Record<string, unknown>): NodeRange | null {
  // DataFrames carry line_start/line_end (also surfaced together as
  // ``line_range`` in some payloads).
  const ls = asNumber(p.line_start);
  const le = asNumber(p.line_end);
  if (ls !== undefined && le !== undefined) {
    return { start: Math.min(ls, le), end: Math.max(ls, le) };
  }
  const lr = asRange(p.line_range);
  if (lr) return { start: lr[0], end: lr[1] };

  // Sources / sinks / connections carry a single ``line``.
  const single = asNumber(p.line);
  if (single !== undefined) return { start: single, end: single };

  return null;
}

function parseChain(raw: unknown): Array<{ seq: number; line?: number; op?: string }> {
  if (typeof raw !== "string" || !raw) return [];
  try {
    const arr = JSON.parse(raw);
    if (!Array.isArray(arr)) return [];
    return arr;
  } catch {
    return [];
  }
}

/**
 * Edges that carry a per-reference ``line`` property the writer stamps when
 * a :DataFrame reads/writes a (shared) :Table or :Connection. The node
 * itself can't hold that line — :Table / :Connection are MERGEd across
 * scripts — so we have to walk these edges to find the in-script call-site
 * for those nodes.
 */
const LINE_BEARING_EDGE_LABELS = new Set<string>([
  "READS_TABLE",
  "WRITES_TABLE",
  "PROVIDES_DATAFRAME",
  "WRITES_TO_CONNECTION",
  "HAS_COLUMN",
]);

function edgeEndpointIdsForLine(
  edge: { data: { source: string; target: string; label: string } },
): { source: string; target: string } {
  return { source: edge.data.source, target: edge.data.target };
}

export function buildLineIndex(data: GraphPayload): LineIndex {
  const nodeRanges = new Map<string, NodeRange>();
  const lineToNodes = new Map<number, Set<string>>();

  const touch = (line: number, id: string) => {
    let bucket = lineToNodes.get(line);
    if (!bucket) {
      bucket = new Set<string>();
      lineToNodes.set(line, bucket);
    }
    bucket.add(id);
  };

  // Build an edge-line index keyed by node id, so we can fall back to
  // edge-attached lines for nodes that have no node-level ``line``. The
  // writer stamps line on READS_TABLE / WRITES_TABLE / PROVIDES_DATAFRAME
  // / WRITES_TO_CONNECTION edges; whichever endpoint is the shared label
  // (Table / Connection / Attribute) inherits the smallest such line.
  const edgeLinesByNode = new Map<string, number>();
  for (const e of data.edges ?? []) {
    if (!LINE_BEARING_EDGE_LABELS.has(e.data.label)) continue;
    const ln = asNumber((e.data.properties as any)?.line);
    if (ln === undefined) continue;
    const { source, target } = edgeEndpointIdsForLine(e);
    for (const endpoint of [source, target]) {
      const prev = edgeLinesByNode.get(endpoint);
      if (prev === undefined || ln < prev) {
        edgeLinesByNode.set(endpoint, ln);
      }
    }
  }

  for (const n of data.nodes) {
    const id = n.data.id;
    const props = (n.data.properties ?? {}) as Record<string, unknown>;
    let range = rangeFromProps(props);

    const chainSteps = parseChain(props.transform_chain)
      .filter((s) => asNumber(s.line) !== undefined)
      .map((s) => ({
        seq: asNumber(s.seq) ?? 0,
        line: asNumber(s.line) as number,
        op: String(s.op ?? ""),
      }));

    // Fallback for nodes that don't carry line on the node itself
    // (:Table / :Connection / :Attribute are shared across scripts via
    // MERGE so the writer puts the line on the edge instead).
    if (!range && chainSteps.length === 0) {
      const edgeLine = edgeLinesByNode.get(id);
      if (edgeLine !== undefined) {
        range = { start: edgeLine, end: edgeLine };
      }
    }

    if (!range && chainSteps.length === 0) continue;

    // Synthesise a covering range from the chain if no explicit range
    // is present (rare but happens for forks pre-collapse fixes).
    const effective: NodeRange = range
      ? { ...range }
      : {
          start: Math.min(...chainSteps.map((s) => s.line)),
          end: Math.max(...chainSteps.map((s) => s.line)),
        };
    if (chainSteps.length > 0) effective.steps = chainSteps;
    nodeRanges.set(id, effective);

    for (let l = effective.start; l <= effective.end; l++) touch(l, id);
    for (const s of chainSteps) touch(s.line, id);
  }

  const finalised = new Map<number, string[]>();
  for (const [line, set] of lineToNodes) {
    finalised.set(line, Array.from(set));
  }
  return { nodeRanges, lineToNodes: finalised };
}

/**
 * Best-guess: which :SparkScript drives the lineage payload? Used when the
 * page opens the source panel for a trace that didn't start at a script.
 *
 * Strategy (cheapest first):
 *   1. an actual :SparkScript node in the payload
 *   2. any spark node carrying ``script_id`` (DataFrames carry this) —
 *      pick the most-referenced
 */
export function pickScriptFileId(data: GraphPayload): string | null {
  for (const n of data.nodes) {
    if (n.data.label === "SparkScript") return n.data.id;
  }
  const counts = new Map<string, number>();
  for (const n of data.nodes) {
    if (n.data.source_system !== "spark") continue;
    const sid = (n.data.properties as any)?.script_id;
    if (typeof sid === "string" && sid) {
      counts.set(sid, (counts.get(sid) ?? 0) + 1);
    }
  }
  let best: string | null = null;
  let bestCount = -1;
  for (const [sid, c] of counts) {
    if (c > bestCount) {
      best = sid;
      bestCount = c;
    }
  }
  return best;
}
