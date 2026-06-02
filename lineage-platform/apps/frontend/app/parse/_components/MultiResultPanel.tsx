"use client";

import {
  Button,
  CodeSnippet,
  DataTable,
  InlineNotification,
  Stack,
  Tag,
  Tile,
} from "@carbon/react";
import { ChartNetwork } from "@carbon/icons-react";
import Link from "next/link";
import {
  CrossFileFollows,
  MultiParseResponse,
  SharedEntity,
} from "../../_lib/api";

const STATUS_TAG: Record<"ok" | "partial" | "failed", "green" | "warm-gray" | "red"> = {
  ok: "green",
  partial: "warm-gray",
  failed: "red",
};

function shortFile(path: string): string {
  const idx = path.lastIndexOf("/");
  return idx >= 0 ? path.slice(idx + 1) : path;
}

export function MultiResultPanel({ result }: { result: MultiParseResponse }) {
  const sharedLabels = Object.keys(result.commonality.shared_entities).sort();
  return (
    <Stack gap={5}>
      <InlineNotification
        kind={
          result.status === "ok"
            ? "success"
            : result.status === "partial"
            ? "warning"
            : "error"
        }
        title={`Multi-parse ${result.status}`}
        subtitle={`${result.files.length} files · ${result.duration_ms} ms`}
        lowContrast
        hideCloseButton
      />

      <Tile>
        <h3 style={{ marginTop: 0, fontSize: "1rem" }}>Per-file results</h3>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid #e0e0e0" }}>
              <th style={{ padding: "0.4rem 0.6rem" }}>File</th>
              <th style={{ padding: "0.4rem 0.6rem" }}>Status</th>
              <th style={{ padding: "0.4rem 0.6rem", textAlign: "right" }}>
                Schedules
              </th>
              <th style={{ padding: "0.4rem 0.6rem", textAlign: "right" }}>
                Jobs
              </th>
              <th style={{ padding: "0.4rem 0.6rem", textAlign: "right" }}>
                Errors
              </th>
            </tr>
          </thead>
          <tbody>
            {result.files.map((f) => (
              <tr
                key={f.file_path}
                style={{ borderBottom: "1px solid #f4f4f4" }}
              >
                <td
                  style={{ padding: "0.4rem 0.6rem", fontFamily: "monospace" }}
                >
                  {shortFile(f.file_path)}
                </td>
                <td style={{ padding: "0.4rem 0.6rem" }}>
                  <Tag type={STATUS_TAG[f.status]} size="sm">
                    {f.status}
                  </Tag>
                </td>
                <td style={{ padding: "0.4rem 0.6rem", textAlign: "right" }}>
                  {f.parsed_schedules}
                </td>
                <td style={{ padding: "0.4rem 0.6rem", textAlign: "right" }}>
                  {f.parsed_jobs}
                </td>
                <td style={{ padding: "0.4rem 0.6rem", textAlign: "right" }}>
                  {f.parse_errors}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Tile>

      <Tile>
        <h3 style={{ marginTop: 0, fontSize: "1rem" }}>Merged topology</h3>
        <CodeSnippet type="multi" feedback="Copied">
          {JSON.stringify(result.merged_stats, null, 2)}
        </CodeSnippet>
      </Tile>

      <Tile>
        <h3 style={{ marginTop: 0, fontSize: "1rem" }}>
          Shared across files ({sharedLabels.length} types)
        </h3>
        {sharedLabels.length === 0 ? (
          <p style={{ color: "#525252" }}>
            No entities are shared between these files — they're entirely
            independent batches.
          </p>
        ) : (
          sharedLabels.map((label) => (
            <SharedLabelSection
              key={label}
              label={label}
              items={result.commonality.shared_entities[label] ?? []}
            />
          ))
        )}
      </Tile>

      <Tile>
        <h3 style={{ marginTop: 0, fontSize: "1rem" }}>
          Cross-file FOLLOWS ({result.commonality.cross_file_follows.length})
        </h3>
        {result.commonality.cross_file_follows.length === 0 ? (
          <p style={{ color: "#525252" }}>
            No FOLLOWS edges span multiple files. Each file's dependency graph
            is self-contained.
          </p>
        ) : (
          <CrossFileFollowsTable
            rows={result.commonality.cross_file_follows}
          />
        )}
      </Tile>

      <Tile>
        <h3 style={{ marginTop: 0, fontSize: "1rem" }}>Next step</h3>
        <Link
          href="/explorer?source_system=tws"
          style={{ textDecoration: "none" }}
        >
          <Button kind="primary" renderIcon={ChartNetwork}>
            Open union in graph explorer
          </Button>
        </Link>
      </Tile>

      {result.warnings && result.warnings.length > 0 && (
        <Tile>
          <h3 style={{ marginTop: 0, fontSize: "1rem" }}>
            Warnings ({result.warnings.length})
          </h3>
          <CodeSnippet type="multi" feedback="Copied">
            {JSON.stringify(result.warnings, null, 2)}
          </CodeSnippet>
        </Tile>
      )}
    </Stack>
  );
}

function SharedLabelSection({
  label,
  items,
}: {
  label: string;
  items: SharedEntity[];
}) {
  if (items.length === 0) return null;
  return (
    <div style={{ marginBottom: "1rem" }}>
      <strong>
        {label} ({items.length})
      </strong>
      <ul style={{ margin: "0.25rem 0 0 1rem" }}>
        {items.map((e) => (
          <li key={e.id}>
            <code>{e.name}</code>{" "}
            <small style={{ color: "#525252" }}>
              in {e.source_files.length} files —{" "}
              {e.source_files.map(shortFile).join(", ")}
            </small>
          </li>
        ))}
      </ul>
    </div>
  );
}

function CrossFileFollowsTable({ rows }: { rows: CrossFileFollows[] }) {
  const headers = [
    { key: "from", header: "Predecessor (from file)" },
    { key: "to", header: "Successor (to file)" },
    { key: "condition", header: "Condition" },
  ];
  const tableRows = rows.map((r, i) => ({
    id: String(i),
    from: `${r.from_job_qualified}  (${shortFile(r.from_file)})`,
    to: `${r.to_job_qualified}  (${shortFile(r.to_file)})`,
    condition: r.condition ?? "—",
  }));
  return (
    <DataTable rows={tableRows} headers={headers}>
      {({
        rows: dtRows,
        headers: dtHeaders,
        getHeaderProps,
        getRowProps,
        getTableProps,
      }: any) => (
        <table
          {...getTableProps()}
          style={{ width: "100%", fontSize: "0.875rem" }}
        >
          <thead>
            <tr>
              {dtHeaders.map((h: any) => (
                <th
                  key={h.key}
                  {...getHeaderProps({ header: h })}
                  style={{ textAlign: "left", padding: "0.4rem 0.6rem" }}
                >
                  {h.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {dtRows.map((row: any) => (
              <tr
                key={row.id}
                {...getRowProps({ row })}
                style={{ borderTop: "1px solid #f4f4f4" }}
              >
                {row.cells.map((c: any) => (
                  <td
                    key={c.id}
                    style={{
                      padding: "0.4rem 0.6rem",
                      fontFamily: "monospace",
                    }}
                  >
                    {c.value}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </DataTable>
  );
}
