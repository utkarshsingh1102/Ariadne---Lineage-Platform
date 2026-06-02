"use client";

import { Button, InlineLoading, Tag, Tile } from "@carbon/react";
import { ArrowRight, Renew } from "@carbon/icons-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { PageHeader } from "./_components/PageHeader";
import { api } from "./_lib/api";

interface DashboardState {
  loading: boolean;
  gatewayStatus?: string;
  neo4j?: string;
  postgres?: string;
  parsers?: Record<string, string>;
  schema?: { labels: string[]; relationship_types: string[]; property_keys: string[] };
  error?: string;
}

export default function DashboardPage() {
  const [state, setState] = useState<DashboardState>({ loading: true });

  async function refresh() {
    setState((s) => ({ ...s, loading: true, error: undefined }));
    try {
      const [health, parsers, schema] = await Promise.all([
        api.health().catch(() => null),
        api.parserHealth().catch(() => ({} as Record<string, string>)),
        api.schema().catch(() => null),
      ]);
      setState({
        loading: false,
        gatewayStatus: health?.status,
        neo4j: health?.neo4j,
        postgres: health?.postgres,
        parsers,
        schema: schema ?? undefined,
      });
    } catch (e: any) {
      setState({ loading: false, error: e?.message ?? String(e) });
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  return (
    <>
      <PageHeader
        title="Lineage Platform"
        subtitle="Cross-parser knowledge graph for Tableau, QlikView, TWS, and Spark."
        breadcrumbs={[{ label: "Home", current: true }]}
        actions={
          <Button
            kind="ghost"
            renderIcon={Renew}
            onClick={refresh}
            disabled={state.loading}
          >
            Refresh
          </Button>
        }
      />

      {state.error && (
        <Tile style={{ marginBottom: "1rem" }}>
          <strong>Failed to reach the gateway.</strong>
          <div style={{ color: "var(--cds-text-secondary)", marginTop: "0.5rem" }}>
            {state.error}
          </div>
        </Tile>
      )}

      <section className="metric-grid">
        <Tile className="metric-tile">
          <p className="metric-tile__label">Gateway</p>
          <div className="metric-tile__value">
            {state.loading ? (
              <InlineLoading description="Checking" />
            ) : (
              <Tag type={state.gatewayStatus === "ok" ? "green" : "red"}>
                {state.gatewayStatus ?? "down"}
              </Tag>
            )}
          </div>
        </Tile>

        <Tile className="metric-tile">
          <p className="metric-tile__label">Neo4j</p>
          <div className="metric-tile__value">
            {state.loading ? (
              <InlineLoading description="Checking" />
            ) : (
              <Tag type={state.neo4j === "connected" ? "green" : "red"}>
                {state.neo4j ?? "unknown"}
              </Tag>
            )}
          </div>
        </Tile>

        <Tile className="metric-tile">
          <p className="metric-tile__label">Postgres</p>
          <div className="metric-tile__value">
            {state.loading ? (
              <InlineLoading description="Checking" />
            ) : (
              <Tag type={state.postgres === "connected" ? "green" : "red"}>
                {state.postgres ?? "unknown"}
              </Tag>
            )}
          </div>
        </Tile>

        <Tile className="metric-tile">
          <p className="metric-tile__label">Graph schema</p>
          <div className="metric-tile__value">
            {state.schema ? state.schema.labels.length : "—"}
          </div>
          <p className="metric-tile__delta">
            {state.schema
              ? `${state.schema.relationship_types.length} relationship types`
              : ""}
          </p>
        </Tile>
      </section>

      <section style={{ marginBottom: "2rem" }}>
        <h2
          style={{
            fontSize: "1.25rem",
            fontWeight: 400,
            marginBottom: "1rem",
            color: "var(--cds-text-primary)",
          }}
        >
          Parser services
        </h2>
        <div className="metric-grid">
          {state.parsers &&
            Object.entries(state.parsers).map(([name, status]) => (
              <Tile key={name} className="metric-tile">
                <p className="metric-tile__label">{name}-parser</p>
                <div className="metric-tile__value">
                  <Tag type={status === "ok" ? "green" : "red"}>{status}</Tag>
                </div>
              </Tile>
            ))}
        </div>
      </section>

      <section>
        <h2
          style={{
            fontSize: "1.25rem",
            fontWeight: 400,
            marginBottom: "1rem",
            color: "var(--cds-text-primary)",
          }}
        >
          Quick links
        </h2>
        <div className="metric-grid">
          {[
            { href: "/explorer", title: "Graph explorer", body: "Browse the full graph by label, search by name, click to expand neighbours." },
            { href: "/lineage", title: "Lineage tracer", body: "Pick a node and trace its upstream or downstream lineage." },
            { href: "/tws", title: "TWS operations", body: "Search Postgres-backed TWS schedules by time window and script." },
            { href: "/parse", title: "Parse a source", body: "Upload a Tableau / QlikView / TWS / Spark file and ingest it into the graph." },
          ].map((c) => (
            <Tile key={c.href}>
              <h3 style={{ fontSize: "1rem", marginTop: 0, color: "var(--cds-text-primary)" }}>
                {c.title}
              </h3>
              <p style={{ color: "var(--cds-text-secondary)", margin: "0.5rem 0 1rem 0" }}>
                {c.body}
              </p>
              <Link href={c.href} style={{ textDecoration: "none" }}>
                <Button kind="ghost" size="sm" renderIcon={ArrowRight}>
                  Open
                </Button>
              </Link>
            </Tile>
          ))}
        </div>
      </section>
    </>
  );
}
