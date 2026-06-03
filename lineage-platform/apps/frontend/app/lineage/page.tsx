"use client";

import {
  Button,
  ContentSwitcher,
  Search,
  Switch,
  Tag,
  ToastNotification,
} from "@carbon/react";
import { ArrowRight, ChevronLeft, ChevronRight } from "@carbon/icons-react";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { PageHeader } from "../_components/PageHeader";
import { SourceLegend } from "../_components/SourceLegend";
import { api, GraphEdge, GraphNode, GraphPayload } from "../_lib/api";

// NOTE: view-source UI was removed deliberately — see the user request in
// the project history. The gateway's ``/files/{source}/{id}/source`` endpoint
// and the per-parser source-code storage remain intact; only the frontend
// affordance to pop a source-code panel on node-tap is gone. If we ever
// reinstate the UI, the unused ``_components/SourceCodePanel.tsx`` and
// ``_lib/line_index.ts`` are still on disk.

const GraphCanvas = dynamic(
  () => import("../_components/GraphCanvas").then((m) => m.GraphCanvas),
  { ssr: false },
);

type Direction = "upstream" | "downstream";

interface SelectedNode {
  id: string;
  label?: string;
  labels?: string[];
  source_system?: string;
  properties?: Record<string, unknown>;
}

const PROP_PRIORITY = [
  "name",
  "fully_qualified_name",
  "path",
  "file_path",
  "location",
  "database",
  "schema",
  "storage_format",
  "datatype",
  "is_calculated",
  "is_derived",
  "formula",
  "role",
  "class",
  "server",
  "dbname",
  "script_type",
  "workstation",
  "start_time",
  "end_time",
  "scheduler",
  "version",
  "has_extract",
  "is_federated",
  "chart_type",
  "scope",
  "return_type",
];

// Properties that the sidebar renders in their own dedicated section, so we
// don't want them duplicated as raw blobs in the generic property bag.
// ``transform_chain`` is rendered by ``TransformChainSection`` below.
const PROPS_RENDERED_ELSEWHERE = new Set<string>(["transform_chain"]);

// TWS timing keys — surfaced in a dedicated ``ScheduleSection`` so users
// don't have to spot them among ids / source_files / raw_definition. The
// section pulls these out for :Schedule / :JobStream / :Job nodes.
const TWS_TIMING_KEYS = new Set<string>([
  "start_time",
  "end_time",
  "deadline",
  "on_until",
  "valid_from",
  "valid_to",
  "run_cycle",
  "run_cycles",
  "cron_equivalent",
  "days_of_week",
  "carry_forward",
  "priority",
  "limit",
  "every",
  "scheduler",
  "workstation",
]);

const DAY_LONG: Record<string, string> = {
  MON: "Mon",
  TUE: "Tue",
  WED: "Wed",
  THU: "Thu",
  FRI: "Fri",
  SAT: "Sat",
  SUN: "Sun",
};

function formatDaysOfWeek(v: unknown): string | null {
  if (Array.isArray(v) && v.length > 0) {
    return v
      .map((d) => DAY_LONG[String(d).toUpperCase()] ?? String(d))
      .join(", ");
  }
  return null;
}

function orderedProps(
  props: Record<string, any>,
  extraSkip?: Set<string>,
): [string, unknown][] {
  const entries = Object.entries(props).filter(
    ([k]) =>
      !PROPS_RENDERED_ELSEWHERE.has(k) && !(extraSkip && extraSkip.has(k)),
  );
  const priority = new Map<string, number>(
    PROP_PRIORITY.map((k, i) => [k, i]),
  );
  return entries.sort(([a], [b]) => {
    const ai = priority.get(a) ?? 999;
    const bi = priority.get(b) ?? 999;
    if (ai !== bi) return ai - bi;
    return a.localeCompare(b);
  });
}

function isTwsScheduleLikeNode(node: SelectedNode | null): boolean {
  if (!node) return false;
  if (node.source_system !== "tws") return false;
  const lbl = (node.label ?? "").toLowerCase();
  if (lbl === "schedule" || lbl === "jobstream" || lbl === "job") return true;
  const labels = (node.labels ?? []).map((l) => l.toLowerCase());
  return (
    labels.includes("schedule") ||
    labels.includes("jobstream") ||
    labels.includes("job")
  );
}

export default function LineagePage() {
  // Wrap the inner page in Suspense — Next 14 requires it whenever the page
  // calls useSearchParams() (the inner body does) so the static build can
  // pre-render shell HTML without the search-params dependency.
  return (
    <Suspense fallback={<div style={{ padding: "1rem" }}>Loading…</div>}>
      <LineagePageInner />
    </Suspense>
  );
}

function LineagePageInner() {
  const params = useSearchParams();

  const [nodeId, setNodeId] = useState("");
  const [direction, setDirection] = useState<Direction>("upstream");
  const [payload, setPayload] = useState<GraphPayload>({ nodes: [], edges: [] });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [filtersOpen, setFiltersOpen] = useState(true);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [selectedNode, setSelectedNode] = useState<SelectedNode | null>(null);
  const [selectedColumns, setSelectedColumns] = useState<
    Array<{ name: string; datatype?: string; is_calculated?: boolean }>
  >([]);
  const [neighbours, setNeighbours] = useState<GraphPayload>({
    nodes: [],
    edges: [],
  });
  const [neighboursLoading, setNeighboursLoading] = useState(false);

  // When the user arrived via Files → "Open combined lineage" or a Project
  // view's "Lineage: whole project", the URL carries ?node_ids=id1,id2,...
  // and we render a banner + the unioned trace instead of the single-node
  // trace.
  const [seedIds, setSeedIds] = useState<string[]>([]);

  // Bumped whenever the parent wants the canvas to imperatively center on
  // a node (e.g. clicking a row in the right-side Connections list).
  const [focusToken, setFocusToken] = useState<{
    id: string;
    ts: number;
  } | null>(null);

  // Resolve the user-entered identifier (id / fqn / path) to the actual
  // Neo4j node id within the trace payload. The preset Cypher matches all
  // three, so the graph node's `id` might not equal what the user typed.
  const resolvedCenterId = useMemo<string | null>(() => {
    if (!nodeId || payload.nodes.length === 0) return null;
    const target = nodeId.trim();
    if (!target) return null;
    for (const n of payload.nodes) {
      const p: any = n.data.properties ?? {};
      if (
        n.data.id === target ||
        p.fully_qualified_name === target ||
        p.path === target ||
        p.file_path === target ||
        p.name === target
      ) {
        return n.data.id;
      }
    }
    return null;
  }, [nodeId, payload]);

  // Deep-linking:
  //   /lineage?node_id=X&direction=upstream  → single-node trace (existing)
  //   /lineage?node_ids=id1,id2,...          → combined-lineage union (new)
  useEffect(() => {
    if (!params) return;
    const qids = params.get("node_ids");
    if (qids) {
      const ids = qids
        .split(",")
        .map((s) => decodeURIComponent(s.trim()))
        .filter(Boolean);
      if (ids.length > 0) {
        setSeedIds(ids);
        loadCombinedLineage(ids);
        return;
      }
    }
    const qid = params.get("node_id");
    const qdir = params.get("direction") as Direction | null;
    if (qdir === "upstream" || qdir === "downstream") setDirection(qdir);
    if (qid) {
      setNodeId(qid);
      trace(qid, qdir ?? undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  async function trace(startId?: string, dirOverride?: Direction) {
    const target = (startId ?? nodeId).trim();
    if (!target) {
      setError("Enter a node id, fully_qualified_name, or script path first.");
      return;
    }
    const dir = dirOverride ?? direction;
    setLoading(true);
    setError(null);
    try {
      const preset =
        dir === "upstream" ? "lineage-upstream" : "lineage-downstream";
      const data = await api.preset(preset, target);
      setPayload(data);
      if (startId) setNodeId(startId);
      // Single-node trace replaces any previous combined-lineage seeding.
      setSeedIds([]);
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setLoading(false);
    }
  }

  // Combined lineage — runs both upstream + downstream presets for every
  // seed id and unions the result. Shared / cross-file connections become
  // edges between the unioned subgraphs automatically because the graph
  // already content-hashes its cross-parser join nodes (Script, Table,
  // Resource) and the TWS cross-file FOLLOWS edges.
  async function loadCombinedLineage(ids: string[]) {
    if (ids.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      const tasks: Promise<GraphPayload | null>[] = [];
      for (const id of ids) {
        tasks.push(api.preset("lineage-upstream", id).catch(() => null));
        tasks.push(api.preset("lineage-downstream", id).catch(() => null));
      }
      const results = await Promise.all(tasks);
      const seenNodes = new Set<string>();
      const seenEdges = new Set<string>();
      const nodes: GraphNode[] = [];
      const edges: GraphEdge[] = [];
      for (const r of results) {
        if (!r) continue;
        for (const n of r.nodes) {
          if (!seenNodes.has(n.data.id)) {
            seenNodes.add(n.data.id);
            nodes.push(n);
          }
        }
        for (const e of r.edges) {
          if (!seenEdges.has(e.data.id)) {
            seenEdges.add(e.data.id);
            edges.push(e);
          }
        }
      }
      setPayload({ nodes, edges });
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setLoading(false);
    }
  }

  async function handleNodeTap(
    id: string,
    detail: { data: any; columns?: Array<{ name: string; datatype?: string; is_calculated?: boolean }> },
  ) {
    const data = detail?.data ?? {};
    setSelectedNode({
      id,
      label: data.label,
      labels: data.labels,
      source_system: data.source_system,
      properties: data.properties ?? {},
    });
    setSelectedColumns(detail?.columns ?? []);
    setDetailsOpen(true);
    setNeighbours({ nodes: [], edges: [] });
    setNeighboursLoading(true);

    try {
      // depth=2 lets us resolve a Table's columns transitively via the
      // DataFrame that writes to it, even when the Table itself has no
      // direct :HAS_COLUMN edges (e.g. a passthrough write).
      const neigh = await api.neighbors(id, 2);
      setNeighbours(neigh);
    } catch {
      // Quiet failure — the rest of the details still render.
    } finally {
      setNeighboursLoading(false);
    }
  }

  // Called when the user clicks a row inside the right-side Connections /
  // Columns lists. Updates the details panel AND nudges the graph to center
  // on the clicked node. Direct cytoscape taps don't go through here — the
  // node is already in view at that point.
  function handleSelectFromList(id: string, data: any) {
    handleNodeTap(id, { data, columns: undefined });
    setFocusToken({ id, ts: Date.now() });
  }

  return (
    <>
      <PageHeader
        title="Lineage tracer"
        subtitle="Trace upstream sources or downstream consumers. Click a node to inspect."
        breadcrumbs={[
          { label: "Home", href: "/" },
          { label: "Lineage tracer", current: true },
        ]}
      />

      {error && (
        <ToastNotification
          kind="error"
          title="Trace failed"
          subtitle={error}
          timeout={5000}
          onClose={() => setError(null)}
        />
      )}

      <div className="lineage-layout">
        {/* ============================== LEFT: filters ============================== */}
        <aside
          className={`lineage-filters ${
            filtersOpen ? "" : "lineage-filters--collapsed"
          }`}
          aria-label="Lineage filters"
        >
          <button
            className="lineage-filters__toggle"
            onClick={() => setFiltersOpen((o) => !o)}
            aria-label={filtersOpen ? "Collapse filters" : "Expand filters"}
            title={filtersOpen ? "Collapse filters" : "Expand filters"}
          >
            {filtersOpen ? (
              <ChevronLeft size={20} />
            ) : (
              <ChevronRight size={20} />
            )}
          </button>

          {filtersOpen && (
            <div className="lineage-filters__content">
              <div className="lineage-sidebar__section">
                <h4>Start node</h4>
                <Search
                  id="node-id-input"
                  labelText="Node id / fully_qualified_name / script path"
                  placeholder="e.g. analytics.dim.customers"
                  size="md"
                  value={nodeId}
                  onChange={(e: any) => setNodeId(e.target.value ?? "")}
                />
              </div>

              <div className="lineage-sidebar__section">
                <h4>Direction</h4>
                <ContentSwitcher
                  onChange={({ name }: any) =>
                    setDirection(name as Direction)
                  }
                  selectedIndex={direction === "upstream" ? 0 : 1}
                  size="md"
                >
                  <Switch name="upstream" text="Upstream" />
                  <Switch name="downstream" text="Downstream" />
                </ContentSwitcher>
              </div>

              <div className="lineage-sidebar__section">
                <Button onClick={() => trace()} disabled={loading} size="md">
                  {loading ? "Tracing…" : "Trace"}
                </Button>
              </div>

              <div className="lineage-sidebar__section">
                <h4>Current graph</h4>
                <div
                  style={{
                    fontSize: "0.75rem",
                    color: "var(--cds-text-secondary)",
                  }}
                >
                  {payload.nodes.length} nodes · {payload.edges.length} edges
                </div>
              </div>

              <div className="lineage-sidebar__section">
                <h4>Legend</h4>
                <SourceLegend />
              </div>

              <div className="lineage-sidebar__section">
                <h4>Tips</h4>
                <p
                  style={{
                    fontSize: "0.75rem",
                    color: "var(--cds-text-secondary)",
                    margin: 0,
                    lineHeight: 1.5,
                  }}
                >
                  Browse the <strong>Files</strong> page to find a starting node,
                  or copy an id from <strong>Graph explorer</strong>. You can
                  also click any node here and then choose &ldquo;Trace from this
                  node&rdquo; in the details panel.
                </p>
              </div>
            </div>
          )}
        </aside>

        {/* ============================== CENTER: graph ============================== */}
        <div className="lineage-layout__center">
          <div className="lineage-layout__graph">
            {seedIds.length > 0 && (
              <div
                style={{
                  background: "var(--cds-layer-01, #f4f4f4)",
                  padding: "0.5rem 0.75rem",
                  borderLeft: "3px solid var(--cds-interactive, #0f62fe)",
                  fontSize: "0.875rem",
                  marginBottom: "0.5rem",
                }}
              >
                <strong>
                  Combined lineage — {seedIds.length} file(s) seeded.
                </strong>
                <span
                  style={{
                    color: "var(--cds-text-secondary)",
                    marginLeft: "0.5rem",
                  }}
                >
                  Cross-file connections appear as edges between the unioned
                  subgraphs.
                </span>
                <Button
                  kind="ghost"
                  size="sm"
                  onClick={() => loadCombinedLineage(seedIds)}
                  style={{ marginLeft: "0.5rem" }}
                  disabled={loading}
                >
                  Refresh union
                </Button>
              </div>
            )}

            {payload.nodes.length === 0 && !loading ? (
              <div className="lineage-empty">
                <div>
                  <p>
                    Use the <strong>filters</strong> panel on the left to start a
                    lineage query.
                  </p>
                  <p style={{ fontSize: "0.75rem", marginTop: "0.75rem" }}>
                    Tip: paste a fully-qualified table name or copy an id from the
                    Graph explorer.
                  </p>
                </div>
              </div>
            ) : (
              <GraphCanvas
                data={payload}
                centerNodeId={resolvedCenterId}
                onNodeTap={handleNodeTap}
                focusToken={focusToken}
              />
            )}
          </div>
        </div>

        {/* ============================== RIGHT: details ============================== */}
        <aside
          className={`lineage-sidebar ${
            detailsOpen ? "" : "lineage-sidebar--collapsed"
          }`}
          aria-label="Node details"
        >
          <button
            className="lineage-sidebar__toggle"
            onClick={() => setDetailsOpen((o) => !o)}
            aria-label={detailsOpen ? "Collapse details" : "Expand details"}
            title={detailsOpen ? "Collapse details" : "Expand details"}
          >
            {detailsOpen ? (
              <ChevronRight size={20} />
            ) : (
              <ChevronLeft size={20} />
            )}
          </button>

          {detailsOpen && (
            <div className="lineage-sidebar__content">
              {selectedNode ? (
                <>
                  <div className="lineage-sidebar__section">
                    <h4>Type</h4>
                    <Tag type="purple">{selectedNode.label}</Tag>{" "}
                    <Tag type="cool-gray">
                      {selectedNode.source_system ?? "unknown"}
                    </Tag>
                  </div>

                  <div className="lineage-sidebar__section">
                    <h4>Identifier</h4>
                    <div
                      className="lineage-sidebar__pre"
                      style={{
                        whiteSpace: "nowrap",
                        overflowX: "auto",
                      }}
                    >
                      {selectedNode.id}
                    </div>
                  </div>

                  {isTwsScheduleLikeNode(selectedNode) && (
                    <ScheduleSection
                      label={selectedNode.label ?? ""}
                      properties={selectedNode.properties ?? {}}
                    />
                  )}

                  <div className="lineage-sidebar__section">
                    <h4>Properties</h4>
                    <dl className="lineage-sidebar__kv">
                      {orderedProps(
                        selectedNode.properties ?? {},
                        isTwsScheduleLikeNode(selectedNode)
                          ? TWS_TIMING_KEYS
                          : undefined,
                      ).map(([k, v]) => (
                        <ProprenderRow key={k} k={k} v={v} />
                      ))}
                    </dl>
                  </div>

                  <TransformChainSection
                    properties={selectedNode.properties ?? {}}
                  />

                  {selectedColumns.length > 0 && (
                    <div className="lineage-sidebar__section">
                      <h4>Columns ({selectedColumns.length})</h4>
                      <ul className="lineage-sidebar__cols">
                        {selectedColumns.map((c) => (
                          <li key={c.name}>
                            <span className="lineage-sidebar__col-name">
                              {c.name}
                            </span>
                            {c.datatype ? (
                              <span className="lineage-sidebar__col-type">
                                {c.datatype}
                              </span>
                            ) : null}
                            {c.is_calculated ? (
                              <span className="lineage-sidebar__col-flag">
                                calc
                              </span>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  <ConnectionsSection
                    selectedId={selectedNode.id}
                    neighbours={neighbours}
                    loading={neighboursLoading}
                    onSelect={handleSelectFromList}
                  />

                  <div className="lineage-sidebar__section">
                    <h4>JSON</h4>
                    <pre className="lineage-sidebar__pre">
                      {JSON.stringify(selectedNode.properties ?? {}, null, 2)}
                    </pre>
                  </div>

                  <div className="lineage-sidebar__section">
                    <h4>Actions</h4>
                    <Button
                      kind="tertiary"
                      size="sm"
                      renderIcon={ArrowRight}
                      onClick={() => trace(selectedNode.id)}
                    >
                      Trace from this node
                    </Button>
                  </div>
                </>
              ) : (
                <p
                  style={{
                    color: "var(--cds-text-secondary)",
                    marginTop: "1rem",
                    fontSize: "0.875rem",
                  }}
                >
                  Click any node in the graph to see its full details here.
                </p>
              )}
            </div>
          )}
        </aside>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Connections / Columns rendering
// ---------------------------------------------------------------------------

interface ConnectionsSectionProps {
  selectedId: string;
  neighbours: GraphPayload;
  loading: boolean;
  onSelect: (id: string, data: any) => void;
}

function ConnectionsSection({
  selectedId,
  neighbours,
  loading,
  onSelect,
}: ConnectionsSectionProps) {
  if (loading) {
    return (
      <div className="lineage-sidebar__section">
        <h4>Connections</h4>
        <p style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)" }}>
          Loading…
        </p>
      </div>
    );
  }

  // Build a lookup of nodeId → node
  const nodeById: Record<string, GraphPayload["nodes"][number]> = {};
  for (const n of neighbours.nodes) {
    nodeById[n.data.id] = n;
  }

  // Group edges by relationship type. Each entry holds the connected node
  // (the "other" endpoint) and a direction marker. With depth=2 the payload
  // can include edges that don't touch the selected node — filter those out
  // of the visible Connections section.
  type Connection = {
    node: GraphPayload["nodes"][number];
    direction: "out" | "in";
    edge: GraphPayload["edges"][number];
  };
  const grouped: Record<string, Connection[]> = {};
  for (const e of neighbours.edges) {
    const isOut = e.data.source === selectedId;
    const isIn = e.data.target === selectedId;
    if (!isOut && !isIn) continue; // 2-hop edge — skip from Connections
    const otherId = isOut ? e.data.target : e.data.source;
    if (otherId === selectedId) continue; // self-loop
    const other = nodeById[otherId];
    if (!other) continue;
    const key = e.data.label || "RELATED";
    (grouped[key] = grouped[key] ?? []).push({
      node: other,
      direction: isOut ? "out" : "in",
      edge: e,
    });
  }

  // Resolve the schema for Tables. Prefer direct HAS_COLUMN children, but
  // fall back to the HAS_FIELD attributes of any DataFrame that writes to
  // this table — those represent the same logical schema.
  const directColumns = (grouped["HAS_COLUMN"] ?? []).map((c) => c.node);
  let columnsViaWriter: GraphPayload["nodes"] = [];
  if (directColumns.length === 0) {
    // DataFrames that WRITES_TABLE → this node
    const writerDfIds = new Set<string>();
    for (const e of neighbours.edges) {
      if (e.data.label === "WRITES_TABLE" && e.data.target === selectedId) {
        writerDfIds.add(e.data.source);
      }
    }
    // HAS_FIELD attrs of those DataFrames
    const dedup: Record<string, GraphPayload["nodes"][number]> = {};
    for (const e of neighbours.edges) {
      if (e.data.label !== "HAS_FIELD") continue;
      if (!writerDfIds.has(e.data.source)) continue;
      const attr = nodeById[e.data.target];
      if (!attr) continue;
      const key = String(attr.data.properties?.name ?? attr.data.id);
      dedup[key] = attr;
    }
    columnsViaWriter = Object.values(dedup);
  }

  const relTypes = Object.keys(grouped).sort();
  if (relTypes.length === 0) {
    return (
      <div className="lineage-sidebar__section">
        <h4>Connections</h4>
        <p style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)" }}>
          No connected nodes found.
        </p>
      </div>
    );
  }

  const otherRels = relTypes.filter((r) => r !== "HAS_COLUMN");

  // What we actually render in the Columns block.
  const renderedColumns =
    directColumns.length > 0 ? directColumns : columnsViaWriter;
  const columnsSource: "direct" | "writer" | "none" =
    directColumns.length > 0
      ? "direct"
      : columnsViaWriter.length > 0
      ? "writer"
      : "none";

  return (
    <>
      <div className="lineage-sidebar__section">
        <h4>
          {columnsSource === "writer"
            ? "Columns (via writer DataFrame)"
            : `Columns${
                renderedColumns.length ? ` (${renderedColumns.length})` : ""
              }`}
        </h4>
        {renderedColumns.length > 0 ? (
          <ul
            style={{
              listStyle: "none",
              margin: 0,
              padding: 0,
              fontSize: "0.8125rem",
            }}
          >
            {renderedColumns.map((node) => {
              const p = node.data.properties ?? {};
              const calc =
                (p.is_calculated as boolean) ?? (p.is_derived as boolean);
              const datatype = p.datatype as string | undefined;
              const formula = p.formula as string | undefined;
              return (
                <li
                  key={node.data.id}
                  className="lineage-sidebar__conn-row"
                  onClick={() => onSelect(node.data.id, node.data)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect(node.data.id, node.data);
                    }
                  }}
                  style={{
                    padding: "0.375rem 0.5rem",
                    borderBottom:
                      "1px solid var(--cds-border-subtle-01)",
                    cursor: "pointer",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "0.5rem",
                    }}
                  >
                    <strong>{String(p.name ?? node.data.id)}</strong>
                    {datatype && (
                      <Tag type="cool-gray" size="sm">
                        {String(datatype)}
                      </Tag>
                    )}
                    {calc && (
                      <Tag type="purple" size="sm">
                        calculated
                      </Tag>
                    )}
                  </div>
                  {formula && (
                    <div
                      style={{
                        fontFamily: "IBM Plex Mono, monospace",
                        fontSize: "0.6875rem",
                        color: "var(--cds-text-secondary)",
                        marginTop: "0.25rem",
                        wordBreak: "break-word",
                      }}
                    >
                      = {String(formula)}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        ) : (
          <p
            style={{
              fontSize: "0.75rem",
              color: "var(--cds-text-secondary)",
              lineHeight: 1.5,
              margin: 0,
            }}
          >
            No column-level schema available for this node. For Spark targets,
            this usually means the writing DataFrame did not enumerate columns
            (e.g. <code>spark.table(...)</code> → <code>filter</code> →
            <code>saveAsTable</code>). Add an explicit{" "}
            <code>select(...)</code> or <code>withColumn(...)</code> in the
            source script to make the schema visible here.
          </p>
        )}
      </div>

      {otherRels.length > 0 && (
        <div className="lineage-sidebar__section">
          <h4>Connections</h4>
          {otherRels.map((rel) => (
            <div key={rel} style={{ marginBottom: "0.75rem" }}>
              <div
                style={{
                  fontSize: "0.6875rem",
                  textTransform: "uppercase",
                  letterSpacing: "0.32px",
                  color: "var(--cds-text-secondary)",
                  marginBottom: "0.25rem",
                }}
              >
                {rel} ({grouped[rel].length})
              </div>
              <ul
                style={{
                  listStyle: "none",
                  margin: 0,
                  padding: 0,
                  fontSize: "0.8125rem",
                }}
              >
                {grouped[rel].map(({ node, direction }) => {
                  const p = node.data.properties ?? {};
                  const name =
                    (p.name as string) ||
                    (p.fully_qualified_name as string) ||
                    (p.path as string) ||
                    node.data.id;
                  return (
                    <li
                      key={node.data.id + direction}
                      className="lineage-sidebar__conn-row"
                      onClick={() => onSelect(node.data.id, node.data)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          onSelect(node.data.id, node.data);
                        }
                      }}
                      title={`Open ${name}`}
                      style={{
                        padding: "0.25rem 0.5rem",
                        cursor: "pointer",
                        borderRadius: "2px",
                      }}
                    >
                      <span
                        style={{
                          color: "var(--cds-text-secondary)",
                          marginRight: "0.375rem",
                        }}
                      >
                        {direction === "out" ? "→" : "←"}
                      </span>
                      <span
                        style={{
                          color: "var(--cds-link-primary)",
                          textDecoration: "underline",
                          textUnderlineOffset: "2px",
                        }}
                      >
                        {name}
                      </span>{" "}
                      <Tag type="outline" size="sm">
                        {node.data.label}
                      </Tag>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

interface TransformStep {
  seq: number;
  op: string;
  kind: string;
  expr?: string | null;
  output_column?: string | null;
  output_columns?: string[];
  input_columns?: string[];
  join_other?: string | null;
  join_keys?: string[];
  join_how?: string | null;
  line?: number | null;
}

const STEP_KIND_TAG: Record<
  string,
  "blue" | "green" | "purple" | "magenta" | "warm-gray" | "cool-gray" | "teal" | "cyan" | "red"
> = {
  derive: "blue",
  cast: "cyan",
  rename: "teal",
  drop: "red",
  filter: "magenta",
  join: "purple",
  agg: "green",
  select: "warm-gray",
  meta: "cool-gray",
};

/**
 * Render the ``transform_chain`` JSON property the spark parser writes
 * onto every anchor :DataFrame node. Plan ``dataframe_collapse_plan.md``
 * §7: clicking a node should reveal the ordered list of ops it folded
 * from intermediates, so users see "the full logic at that instant".
 */
function TransformChainSection({
  properties,
}: {
  properties: Record<string, unknown>;
}) {
  const raw = properties?.["transform_chain"];
  if (typeof raw !== "string" || !raw) return null;
  let chain: TransformStep[];
  try {
    chain = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!Array.isArray(chain) || chain.length === 0) return null;
  return (
    <div className="lineage-sidebar__section">
      <h4>Transformation chain ({chain.length})</h4>
      <ol className="transform-chain">
        {chain.map((step) => (
          <li key={step.seq} className="transform-chain__step">
            <div className="transform-chain__head">
              <span className="transform-chain__seq">#{step.seq}</span>
              <Tag type={STEP_KIND_TAG[step.kind] ?? "cool-gray"}>
                {step.op}
              </Tag>
              <Tag type="outline">{step.kind}</Tag>
              {typeof step.line === "number" && (
                <span className="transform-chain__line">L{step.line}</span>
              )}
            </div>
            {(step.input_columns?.length ?? 0) > 0 ||
            step.output_column ||
            (step.output_columns?.length ?? 0) > 0 ? (
              <div className="transform-chain__cols">
                {step.input_columns && step.input_columns.length > 0 && (
                  <span className="transform-chain__col-set">
                    in: {step.input_columns.join(", ")}
                  </span>
                )}
                {step.output_column && (
                  <span className="transform-chain__col-set">
                    out: {step.output_column}
                  </span>
                )}
                {step.output_columns && step.output_columns.length > 0 && (
                  <span className="transform-chain__col-set">
                    out: {step.output_columns.join(", ")}
                  </span>
                )}
              </div>
            ) : null}
            {step.join_other && (
              <div className="transform-chain__join">
                join {step.join_how ?? ""} ← {step.join_other}
                {step.join_keys && step.join_keys.length > 0
                  ? ` on (${step.join_keys.join(", ")})`
                  : ""}
              </div>
            )}
            {step.expr && (
              <pre className="transform-chain__expr">{step.expr}</pre>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

// TWS Schedule & Timing — rendered for :Schedule / :JobStream / :Job nodes.
// Always renders when one of these labels is selected, even if every field
// is null, so the user can SEE that timing wasn't specified rather than
// wonder if the parser dropped it.
function ScheduleSection({
  label,
  properties,
}: {
  label: string;
  properties: Record<string, unknown>;
}) {
  const fmt = (v: unknown): string => {
    if (v === null || v === undefined || v === "") return "—";
    if (typeof v === "boolean") return v ? "yes" : "no";
    return String(v);
  };

  const isJob = label.toLowerCase() === "job";

  // run_cycles is JSON-encoded on JobStream nodes; tolerate either array
  // or string form.
  const runCyclesRaw = properties["run_cycles"];
  let runCycles: Array<{
    name?: string;
    rrule?: string;
    calendar_name?: string;
    is_except?: boolean;
  }> = [];
  if (Array.isArray(runCyclesRaw)) {
    runCycles = runCyclesRaw as typeof runCycles;
  } else if (typeof runCyclesRaw === "string" && runCyclesRaw.trim()) {
    try {
      const parsed = JSON.parse(runCyclesRaw);
      if (Array.isArray(parsed)) runCycles = parsed;
    } catch {
      /* leave empty */
    }
  }

  const rows: Array<[string, unknown]> = [];
  if (!isJob) {
    rows.push(["Workstation", properties["workstation"]]);
    rows.push(["Scheduler", properties["scheduler"]]);
    rows.push(["Run cycle (raw)", properties["run_cycle"]]);
    rows.push([
      "Days",
      formatDaysOfWeek(properties["days_of_week"]),
    ]);
    rows.push(["Cron equivalent", properties["cron_equivalent"]]);
    rows.push(["Start time (AT)", properties["start_time"]]);
    rows.push(["End time (UNTIL)", properties["end_time"]]);
    rows.push(["Deadline", properties["deadline"]]);
    rows.push(["On-until action", properties["on_until"]]);
    rows.push(["Valid from", properties["valid_from"]]);
    rows.push(["Valid to", properties["valid_to"]]);
    rows.push(["Priority", properties["priority"]]);
    rows.push(["Carry forward", properties["carry_forward"]]);
    rows.push(["Rerun cadence (every, min)", properties["every"]]);
    rows.push(["Limit", properties["limit"]]);
  } else {
    rows.push(["Workstation", properties["workstation"]]);
    rows.push(["Stream", properties["stream"]]);
    rows.push(["Priority", properties["priority"]]);
    rows.push(["Rerun cadence (every, min)", properties["every"]]);
    rows.push(["Order in schedule", properties["order_in_schedule"]]);
  }

  return (
    <div className="lineage-sidebar__section">
      <h4>Schedule &amp; timing</h4>
      <dl className="lineage-sidebar__kv">
        {rows.map(([k, v]) => (
          <ProprenderRow key={k} k={k} v={fmt(v)} />
        ))}
      </dl>
      {runCycles.length > 0 && (
        <div style={{ marginTop: "0.75rem" }}>
          <h5
            style={{
              fontSize: "0.8125rem",
              margin: "0 0 0.375rem 0",
              color: "var(--cds-text-secondary)",
            }}
          >
            Run cycles ({runCycles.length})
          </h5>
          <ul className="lineage-sidebar__cols">
            {runCycles.map((rc, i) => (
              <li key={i}>
                <span className="lineage-sidebar__col-name">
                  {rc.is_except ? "EXCEPT " : ""}
                  {rc.name ?? "(unnamed)"}
                </span>
                {rc.calendar_name && (
                  <span className="lineage-sidebar__col-type">
                    calendar: {rc.calendar_name}
                  </span>
                )}
                {rc.rrule && (
                  <span
                    className="lineage-sidebar__col-type"
                    style={{ wordBreak: "break-all" }}
                  >
                    {rc.rrule}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// Heuristic: a string that starts with ``{`` or ``[`` is almost certainly a
// JSON blob the writer serialised onto a Neo4j property — pretty-print it
// instead of showing it as a single wrapped line.
function maybeParseJson(s: string): unknown | undefined {
  const trimmed = s.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return undefined;
  try {
    return JSON.parse(trimmed);
  } catch {
    return undefined;
  }
}

function ProprenderRow({ k, v }: { k: string; v: unknown }) {
  if (v === null || v === undefined) {
    return (
      <>
        <dt>{k}</dt>
        <dd>—</dd>
      </>
    );
  }

  if (typeof v === "object") {
    return (
      <>
        <dt>{k}</dt>
        <dd>
          <pre className="lineage-sidebar__json">
            {JSON.stringify(v, null, 2)}
          </pre>
        </dd>
      </>
    );
  }

  if (typeof v === "string") {
    const parsed = maybeParseJson(v);
    if (parsed !== undefined) {
      return (
        <>
          <dt>{k}</dt>
          <dd>
            <pre className="lineage-sidebar__json">
              {JSON.stringify(parsed, null, 2)}
            </pre>
          </dd>
        </>
      );
    }
  }

  return (
    <>
      <dt>{k}</dt>
      <dd>{String(v)}</dd>
    </>
  );
}
