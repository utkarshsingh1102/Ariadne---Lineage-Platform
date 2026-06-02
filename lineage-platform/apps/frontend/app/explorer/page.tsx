"use client";

import {
  Button,
  Dropdown,
  Modal,
  Search,
  Stack,
  Tag,
  Tile,
  ToastNotification,
} from "@carbon/react";
import { Renew, Search as SearchIcon } from "@carbon/icons-react";
import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { PageHeader } from "../_components/PageHeader";
import { SourceLegend } from "../_components/SourceLegend";
import { api, GraphPayload } from "../_lib/api";

const GraphCanvas = dynamic(
  () => import("../_components/GraphCanvas").then((m) => m.GraphCanvas),
  { ssr: false },
);

export default function ExplorerPage() {
  const [labels, setLabels] = useState<string[]>([]);
  const [selectedLabel, setSelectedLabel] = useState<string | null>(null);
  const [nameLike, setNameLike] = useState("");
  const [payload, setPayload] = useState<GraphPayload>({ nodes: [], edges: [] });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<any | null>(null);

  useEffect(() => {
    api
      .schema()
      .then((s) => setLabels(s.labels))
      .catch(() => setLabels([]));
  }, []);

  async function runFilter() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.listNodes({
        label: selectedLabel ?? undefined,
        name_like: nameLike || undefined,
        limit: 100,
      });
      setPayload(data);
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setLoading(false);
    }
  }

  async function expandNode(
    nodeId: string,
    detail: { data: any; columns?: any },
  ) {
    const data = detail?.data ?? {};
    setLoading(true);
    try {
      const neigh = await api.neighbors(nodeId, 1);
      // Merge new nodes/edges into the existing payload
      const seenNodes = new Set(payload.nodes.map((n) => n.data.id));
      const seenEdges = new Set(payload.edges.map((e) => e.data.id));
      setPayload({
        nodes: [
          ...payload.nodes,
          ...neigh.nodes.filter((n) => !seenNodes.has(n.data.id)),
        ],
        edges: [
          ...payload.edges,
          ...neigh.edges.filter((e) => !seenEdges.has(e.data.id)),
        ],
      });
      setDetail({ id: nodeId, ...data });
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setLoading(false);
    }
  }

  const stats = useMemo(
    () => `${payload.nodes.length} nodes · ${payload.edges.length} edges`,
    [payload],
  );

  return (
    <>
      <PageHeader
        title="Graph explorer"
        subtitle="Browse every node in the lineage graph. Click a node to expand its neighbours."
        breadcrumbs={[
          { label: "Home", href: "/" },
          { label: "Graph explorer", current: true },
        ]}
        actions={
          <Button
            kind="primary"
            renderIcon={SearchIcon}
            onClick={runFilter}
            disabled={loading}
          >
            Apply filter
          </Button>
        }
      />

      {error && (
        <ToastNotification
          kind="error"
          title="Gateway error"
          subtitle={error}
          timeout={6000}
          onClose={() => setError(null)}
        />
      )}

      <Stack gap={4} style={{ marginBottom: "1rem" }}>
        <div className="graph-toolbar">
          <Dropdown
            id="label-filter"
            titleText="Label"
            label="Any label"
            items={["", ...labels]}
            itemToString={(i: string) => (i === "" ? "Any label" : i)}
            selectedItem={selectedLabel ?? ""}
            onChange={({ selectedItem }: any) =>
              setSelectedLabel(selectedItem || null)
            }
          />
          <Search
            id="name-search"
            labelText="Name contains"
            placeholder="orders, sales_fact, …"
            size="lg"
            value={nameLike}
            onChange={(e: any) => setNameLike(e.target.value ?? "")}
          />
          <Button kind="ghost" renderIcon={Renew} onClick={runFilter}>
            Refresh
          </Button>
        </div>
        <SourceLegend />
        <div style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)" }}>
          {loading ? "Loading…" : stats}
        </div>
      </Stack>

      <GraphCanvas data={payload} onNodeTap={expandNode} />

      {detail && (
        <Modal
          open
          modalHeading={`${detail.label ?? "Node"}: ${detail.id}`}
          primaryButtonText="Close"
          onRequestSubmit={() => setDetail(null)}
          onRequestClose={() => setDetail(null)}
          passiveModal
        >
          <Tile>
            <div style={{ marginBottom: "1rem" }}>
              <Tag type="blue">{detail.label}</Tag>{" "}
              <Tag type="purple">{detail.source_system}</Tag>
            </div>
            <pre
              style={{
                fontFamily: "IBM Plex Mono, monospace",
                fontSize: "0.75rem",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                color: "var(--cds-text-secondary)",
              }}
            >
              {JSON.stringify(detail.properties ?? {}, null, 2)}
            </pre>
          </Tile>
        </Modal>
      )}
    </>
  );
}
