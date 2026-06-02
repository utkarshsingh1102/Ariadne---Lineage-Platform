"use client";

import {
  Button,
  DataTable,
  DataTableSkeleton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
  TextInput,
  Tile,
  ToastNotification,
} from "@carbon/react";
import { Filter, Renew } from "@carbon/icons-react";
import { useState } from "react";
import { PageHeader } from "../_components/PageHeader";
import { api, TwsJob } from "../_lib/api";

const HEADERS = [
  { key: "job_name", header: "Job" },
  { key: "workstation", header: "Workstation" },
  { key: "start_time", header: "Start" },
  { key: "end_time", header: "End" },
  { key: "script_path", header: "Script" },
  { key: "schedule_name", header: "Schedule" },
];

export default function TwsPage() {
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");
  const [scriptLike, setScriptLike] = useState("");
  const [workstation, setWorkstation] = useState("");
  const [rows, setRows] = useState<TwsJob[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [didSearch, setDidSearch] = useState(false);

  async function runSearch() {
    setLoading(true);
    setError(null);
    try {
      const res = await api.twsJobs({
        start_time: startTime || undefined,
        end_time: endTime || undefined,
        script_path_like: scriptLike || undefined,
        workstation: workstation || undefined,
        limit: 500,
      });
      setRows(res.rows);
      setDidSearch(true);
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setLoading(false);
    }
  }

  // Carbon DataTable expects `id` per row.
  const rowsForTable = rows.map((r, i) => ({
    id: `${r.job_name}-${i}`,
    ...r,
  }));

  return (
    <>
      <PageHeader
        title="TWS operations"
        subtitle="Search Postgres-backed TWS schedules by time window, script path, or workstation."
        breadcrumbs={[
          { label: "Home", href: "/" },
          { label: "TWS operations", current: true },
        ]}
        actions={
          <Button
            kind="primary"
            renderIcon={Filter}
            onClick={runSearch}
            disabled={loading}
          >
            Search
          </Button>
        }
      />

      {error && (
        <ToastNotification
          kind="error"
          title="TWS query failed"
          subtitle={error}
          timeout={6000}
          onClose={() => setError(null)}
        />
      )}

      <Stack gap={4} style={{ marginBottom: "1.5rem" }}>
        <div className="graph-toolbar">
          <TextInput
            id="start-time"
            labelText="Start time (HH:MM)"
            placeholder="05:30"
            value={startTime}
            onChange={(e: any) => setStartTime(e.target.value)}
          />
          <TextInput
            id="end-time"
            labelText="End time (HH:MM)"
            placeholder="06:30"
            value={endTime}
            onChange={(e: any) => setEndTime(e.target.value)}
          />
          <TextInput
            id="script-like"
            labelText="Script path contains"
            placeholder="load_orders"
            value={scriptLike}
            onChange={(e: any) => setScriptLike(e.target.value)}
          />
          <TextInput
            id="workstation"
            labelText="Workstation"
            placeholder="WS01"
            value={workstation}
            onChange={(e: any) => setWorkstation(e.target.value)}
          />
          <Button kind="ghost" renderIcon={Renew} onClick={runSearch}>
            Refresh
          </Button>
        </div>
      </Stack>

      {loading ? (
        <DataTableSkeleton
          headers={HEADERS as any}
          rowCount={8}
          showHeader={false}
          showToolbar={false}
        />
      ) : !didSearch ? (
        <Tile>
          <p style={{ color: "var(--cds-text-secondary)" }}>
            Enter any combination of filters above and click <em>Search</em>.
            Empty filters match everything; the gateway caps results at 500 rows.
          </p>
        </Tile>
      ) : rows.length === 0 ? (
        <Tile>
          <p style={{ color: "var(--cds-text-secondary)" }}>
            No TWS jobs matched. Try widening the time window or clearing the
            script-path filter.
          </p>
        </Tile>
      ) : (
        <DataTable rows={rowsForTable as any} headers={HEADERS}>
          {({
            rows: tRows,
            headers: tHeaders,
            getHeaderProps,
            getRowProps,
            getTableProps,
          }: any) => (
            <TableContainer
              title={`${rows.length} job${rows.length === 1 ? "" : "s"}`}
              description="Powered by the tws.v_runtime_window view in Postgres."
            >
              <Table {...getTableProps()} size="sm">
                <TableHead>
                  <TableRow>
                    {tHeaders.map((h: any) => (
                      <TableHeader {...getHeaderProps({ header: h })} key={h.key}>
                        {h.header}
                      </TableHeader>
                    ))}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {tRows.map((row: any) => (
                    <TableRow {...getRowProps({ row })} key={row.id}>
                      {row.cells.map((cell: any) => (
                        <TableCell key={cell.id}>{cell.value ?? "—"}</TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </DataTable>
      )}
    </>
  );
}
