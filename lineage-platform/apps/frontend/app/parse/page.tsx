"use client";

import {
  Button,
  ButtonSet,
  Checkbox,
  CodeSnippet,
  ContentSwitcher,
  FileUploader,
  InlineNotification,
  RadioButton,
  RadioButtonGroup,
  Stack,
  Switch,
  Tag,
  TextInput,
  Tile,
  ToastNotification,
} from "@carbon/react";
import { ArrowRight, CloudUpload, Folder } from "@carbon/icons-react";
import Link from "next/link";
import { useState } from "react";
import { PageHeader } from "../_components/PageHeader";
import {
  api,
  AutoBatchResponse,
  MultiParseResponse,
  ParseResponse,
} from "../_lib/api";
import { MultiResultPanel } from "./_components/MultiResultPanel";

type SourceType = "tableau" | "qlikview" | "tws" | "spark";
type Mode = "single" | "multi" | "auto";

// Reverse-lookup of file extension → parser. Mirror of the gateway's
// _SUFFIX_TO_SOURCE map so the user gets per-file detection feedback BEFORE
// they hit submit. The gateway is authoritative; this is just a preview.
const SUFFIX_TO_SOURCE: Record<string, SourceType> = {
  ".twb": "tableau",
  ".twbx": "tableau",
  ".txt": "tws",
  ".xml": "tws",
  ".qvs": "qlikview",
  ".qvw": "qlikview",
  ".qvf": "qlikview",
  ".py": "spark",
  ".sql": "spark",
  ".ipynb": "spark",
  ".dbc": "spark",
};

const ALL_ACCEPTED_SUFFIXES = Object.keys(SUFFIX_TO_SOURCE);

function detectSourceClient(filename: string): SourceType | null {
  const dot = filename.lastIndexOf(".");
  if (dot < 0) return null;
  const suffix = filename.slice(dot).toLowerCase();
  return SUFFIX_TO_SOURCE[suffix] ?? null;
}

const SOURCE_TAG_TYPE: Record<SourceType, string> = {
  tableau: "blue",
  qlikview: "green",
  tws: "magenta",
  spark: "warm-gray",
};

const ACCEPT: Record<SourceType, string[]> = {
  tableau: [".twb", ".twbx"],
  qlikview: [".qvs", ".qvw", ".qvf"],
  tws: [".txt", ".xml"],
  spark: [".py", ".sql", ".ipynb", ".dbc"],
};

const HINTS: Record<SourceType, string> = {
  tableau: "Tableau workbook — .twb / .twbx",
  qlikview: "QlikView load script — .qvs (or .qvw / .qvf)",
  tws: "TWS schedule export — .txt / .xml",
  spark: "PySpark / Spark SQL / Databricks notebook — .py / .sql / .ipynb / .dbc",
};

type ResultBody = ParseResponse & {
  uploaded_as?: string;
  original_filename?: string;
};

export default function ParsePage() {
  const [mode, setMode] = useState<Mode>("single");

  // ----- single-file state ------------------------------------------------
  const [sourceType, setSourceType] = useState<SourceType>("tableau");
  const [file, setFile] = useState<File | null>(null);
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ResultBody | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploaderKey, setUploaderKey] = useState(0);

  // ----- multi-file state -------------------------------------------------
  const [multiFiles, setMultiFiles] = useState<File[]>([]);
  const [multiResult, setMultiResult] = useState<MultiParseResponse | null>(
    null,
  );
  const [multiUploaderKey, setMultiUploaderKey] = useState(0);

  // ----- auto-batch (mixed-source) state ----------------------------------
  const [autoFiles, setAutoFiles] = useState<File[]>([]);
  const [autoResult, setAutoResult] = useState<AutoBatchResponse | null>(null);
  const [autoUploaderKey, setAutoUploaderKey] = useState(0);

  // ----- project-grouping state (only used in mixed-batch mode) -----------
  type Grouping = "source" | "project";
  const [grouping, setGrouping] = useState<Grouping>("source");
  const [projectName, setProjectName] = useState("");

  const resetUploader = () => {
    setFile(null);
    setUploaderKey((k) => k + 1);
  };

  const resetMultiUploader = () => {
    setMultiFiles([]);
    setMultiUploaderKey((k) => k + 1);
  };

  const resetAutoUploader = () => {
    setAutoFiles([]);
    setAutoUploaderKey((k) => k + 1);
  };

  function handleModeChange(next: Mode) {
    setMode(next);
    setError(null);
    setResult(null);
    setMultiResult(null);
    setAutoResult(null);
    resetUploader();
    resetMultiUploader();
    resetAutoUploader();
    // Multi mode is TWS-only — lock the source type so the rest of the
    // UI shows the right hints + accept filter even if the user toggled
    // back to single later.
    if (next === "multi") setSourceType("tws");
  }

  function handleFileAdded(e: any, content: any) {
    const picked: File | undefined =
      e?.target?.files?.[0] ??
      e?.target?.addedFiles?.[0]?.file ??
      content?.addedFiles?.[0]?.file ??
      content?.addedFiles?.[0];
    if (picked instanceof File) {
      setFile(picked);
      setError(null);
    }
  }

  function handleMultiFilesAdded(e: any, content: any) {
    const fromTargetFiles: File[] = Array.from(e?.target?.files ?? []);
    const fromTargetAdded: File[] = (e?.target?.addedFiles ?? [])
      .map((it: any) => it?.file)
      .filter(Boolean);
    const fromContent: File[] = (content?.addedFiles ?? [])
      .map((it: any) => it?.file ?? it)
      .filter((f: any) => f instanceof File);
    const picked = [...fromTargetFiles, ...fromTargetAdded, ...fromContent];
    if (picked.length === 0) return;
    setMultiFiles((prev) => {
      const key = (f: File) => `${f.name}::${f.size}::${f.lastModified}`;
      const seen = new Set(prev.map(key));
      const next = [...prev];
      for (const f of picked) {
        if (!seen.has(key(f))) {
          next.push(f);
          seen.add(key(f));
        }
      }
      return next;
    });
    setError(null);
  }

  function handleAutoFilesAdded(e: any, content: any) {
    const fromTargetFiles: File[] = Array.from(e?.target?.files ?? []);
    const fromTargetAdded: File[] = (e?.target?.addedFiles ?? [])
      .map((it: any) => it?.file)
      .filter(Boolean);
    const fromContent: File[] = (content?.addedFiles ?? [])
      .map((it: any) => it?.file ?? it)
      .filter((f: any) => f instanceof File);
    const picked = [...fromTargetFiles, ...fromTargetAdded, ...fromContent];
    if (picked.length === 0) return;
    setAutoFiles((prev) => {
      const key = (f: File) => `${f.name}::${f.size}::${f.lastModified}`;
      const seen = new Set(prev.map(key));
      const next = [...prev];
      for (const f of picked) {
        if (!seen.has(key(f))) {
          next.push(f);
          seen.add(key(f));
        }
      }
      return next;
    });
    setError(null);
  }

  function handleSourceChange(v: SourceType) {
    setSourceType(v);
    resetUploader();
  }

  async function submit() {
    setError(null);
    if (mode === "single") {
      if (!file) {
        setError("Choose a file first.");
        return;
      }
      setBusy(true);
      setResult(null);
      try {
        const r = await api.parseUpload(sourceType, file, overwrite);
        setResult(r);
        resetUploader();
      } catch (e: any) {
        setError(e?.message ?? String(e));
      } finally {
        setBusy(false);
      }
    } else if (mode === "multi") {
      if (multiFiles.length < 2) {
        setError("Select at least 2 files to compare.");
        return;
      }
      setBusy(true);
      setMultiResult(null);
      try {
        const r = await api.parseUploadMulti("tws", multiFiles, overwrite);
        setMultiResult(r);
        resetMultiUploader();
      } catch (e: any) {
        setError(e?.message ?? String(e));
      } finally {
        setBusy(false);
      }
    } else {
      // Mixed batch — every file routed to its parser by extension.
      if (autoFiles.length === 0) {
        setError("Add at least one file.");
        return;
      }
      if (grouping === "project" && !projectName.trim()) {
        setError("Project name is required when grouping into a project.");
        return;
      }
      setBusy(true);
      setAutoResult(null);
      try {
        const r = await api.parseUploadAuto(
          autoFiles,
          overwrite,
          grouping === "project" ? projectName : undefined,
        );
        setAutoResult(r);
        resetAutoUploader();
        if (grouping === "project") setProjectName("");
      } catch (e: any) {
        setError(e?.message ?? String(e));
      } finally {
        setBusy(false);
      }
    }
  }

  const submitDisabled =
    busy ||
    (mode === "single"
      ? !file
      : mode === "multi"
      ? multiFiles.length < 2
      : autoFiles.length === 0);
  const submitLabel = busy
    ? mode === "single"
      ? "Parsing…"
      : mode === "multi"
      ? "Analyzing…"
      : "Dispatching…"
    : mode === "single"
    ? "Parse"
    : mode === "multi"
    ? `Analyze ${multiFiles.length} file(s)`
    : `Parse ${autoFiles.length} file(s)`;

  return (
    <>
      <PageHeader
        title="Parse a source"
        subtitle={
          mode === "single"
            ? "Upload a file. The gateway saves it to a shared volume and dispatches it to the right parser."
            : "Upload 2-20 TWS composer files together. The parser merges them, resolves cross-file FOLLOWS edges that solo parses miss, and reports what's shared between files."
        }
        breadcrumbs={[
          { label: "Home", href: "/" },
          { label: "Parse a source", current: true },
        ]}
        actions={
          <Button
            kind="primary"
            renderIcon={CloudUpload}
            onClick={submit}
            disabled={submitDisabled}
          >
            {submitLabel}
          </Button>
        }
      />

      {error && (
        <ToastNotification
          kind="error"
          title={mode === "single" ? "Parse failed" : "Multi-parse failed"}
          subtitle={error}
          timeout={8000}
          onClose={() => setError(null)}
        />
      )}

      <Stack gap={5} style={{ maxWidth: "720px", marginBottom: "2rem" }}>
        <ContentSwitcher
          selectedIndex={
            mode === "single" ? 0 : mode === "multi" ? 1 : 2
          }
          onChange={(e: any) =>
            handleModeChange(
              e.index === 1 ? "multi" : e.index === 2 ? "auto" : "single",
            )
          }
        >
          <Switch name="single" text="Single file" />
          <Switch name="multi" text="Multi-file (TWS)" />
          <Switch name="auto" text="Mixed batch (auto-detect)" />
        </ContentSwitcher>

        {mode === "auto" ? (
          <>
            <FileUploader
              key={autoUploaderKey}
              labelTitle="Upload mixed files"
              labelDescription="Drop any combination of Tableau, TWS, QlikView, or Spark files — the system identifies each one by extension and routes it to the right parser."
              buttonLabel="Choose files"
              buttonKind="tertiary"
              accept={ALL_ACCEPTED_SUFFIXES}
              multiple
              filenameStatus="edit"
              iconDescription="Clear file"
              onAddFiles={handleAutoFilesAdded}
              onChange={handleAutoFilesAdded}
              onDelete={() => resetAutoUploader()}
            />

            {autoFiles.length > 0 && (
              <Tile>
                <strong>{autoFiles.length} file(s) queued — detection preview:</strong>
                <ul style={{ margin: "0.5rem 0 0 0", listStyle: "none", padding: 0 }}>
                  {autoFiles.map((f) => {
                    const detected = detectSourceClient(f.name);
                    return (
                      <li
                        key={`${f.name}::${f.size}::${f.lastModified}`}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: "0.5rem",
                          padding: "0.25rem 0",
                        }}
                      >
                        <span
                          style={{
                            fontFamily: "monospace",
                            flex: "1 1 auto",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {f.name}
                        </span>
                        {detected ? (
                          <Tag
                            type={SOURCE_TAG_TYPE[detected] as any}
                            size="sm"
                          >
                            → {detected}
                          </Tag>
                        ) : (
                          <Tag type="red" size="sm">
                            unsupported
                          </Tag>
                        )}
                        <small style={{ color: "var(--cds-text-secondary)" }}>
                          ({(f.size / 1024).toFixed(1)} KB)
                        </small>
                      </li>
                    );
                  })}
                </ul>
              </Tile>
            )}

            {autoFiles.length >= 2 && (
              <Tile>
                <RadioButtonGroup
                  legendText="Where should these files go?"
                  name="grouping"
                  orientation="vertical"
                  valueSelected={grouping}
                  onChange={(v: any) => setGrouping(v as Grouping)}
                >
                  <RadioButton
                    labelText="Route each file to its source folder (default)"
                    value="source"
                    id="grp-source"
                  />
                  <RadioButton
                    labelText="Group all files into a new project (per-parser subfolders auto-created)"
                    value="project"
                    id="grp-project"
                  />
                </RadioButtonGroup>
                {grouping === "project" && (
                  <div style={{ marginTop: "0.75rem" }}>
                    <TextInput
                      id="project-name"
                      labelText="Project name"
                      placeholder="e.g. acme_q3_data_deck"
                      value={projectName}
                      onChange={(e: any) => setProjectName(e.target.value)}
                      helperText="Must be unique across all projects. Files will be sub-grouped by parser type inside this project."
                    />
                  </div>
                )}
              </Tile>
            )}

            <Checkbox
              id="overwrite-auto"
              labelText="Overwrite — delete existing nodes per source before re-ingesting"
              checked={overwrite}
              onChange={(_: any, { checked }: any) => setOverwrite(!!checked)}
            />
          </>
        ) : mode === "single" ? (
          <>
            <RadioButtonGroup
              legendText="Source type"
              name="source-type"
              orientation="horizontal"
              valueSelected={sourceType}
              onChange={(v: any) => handleSourceChange(v as SourceType)}
            >
              <RadioButton labelText="Tableau" value="tableau" id="src-tab" />
              <RadioButton labelText="QlikView" value="qlikview" id="src-qv" />
              <RadioButton labelText="TWS" value="tws" id="src-tws" />
              <RadioButton labelText="Spark" value="spark" id="src-spark" />
            </RadioButtonGroup>

            <FileUploader
              key={uploaderKey}
              labelTitle="Upload file"
              labelDescription={HINTS[sourceType]}
              buttonLabel="Choose file"
              buttonKind="tertiary"
              accept={ACCEPT[sourceType]}
              multiple={false}
              filenameStatus="edit"
              iconDescription="Clear file"
              onAddFiles={handleFileAdded}
              onChange={handleFileAdded}
              onDelete={() => resetUploader()}
            />

            <Checkbox
              id="overwrite"
              labelText="Overwrite — delete existing nodes for this source before re-ingesting"
              checked={overwrite}
              onChange={(_: any, { checked }: any) => setOverwrite(!!checked)}
            />
          </>
        ) : (
          <>
            <FileUploader
              key={multiUploaderKey}
              labelTitle="Upload TWS files"
              labelDescription="2-20 .txt or .xml composer files — analyzed together to find what's shared."
              buttonLabel="Choose files"
              buttonKind="tertiary"
              accept={ACCEPT.tws}
              multiple
              filenameStatus="edit"
              iconDescription="Clear file"
              onAddFiles={handleMultiFilesAdded}
              onChange={handleMultiFilesAdded}
              onDelete={() => resetMultiUploader()}
            />

            {multiFiles.length > 0 && (
              <Tile>
                <strong>{multiFiles.length} file(s) queued:</strong>
                <ul style={{ margin: "0.5rem 0 0 1rem" }}>
                  {multiFiles.map((f) => (
                    <li key={`${f.name}::${f.size}::${f.lastModified}`}>
                      {f.name}{" "}
                      <small>({(f.size / 1024).toFixed(1)} KB)</small>
                    </li>
                  ))}
                </ul>
              </Tile>
            )}

            <Checkbox
              id="overwrite-multi"
              labelText="Overwrite — delete existing TWS nodes before re-ingesting"
              checked={overwrite}
              onChange={(_: any, { checked }: any) => setOverwrite(!!checked)}
            />
          </>
        )}
      </Stack>

      {mode === "single" && result && (
        <>
          <InlineNotification
            kind="success"
            title="Parse complete"
            subtitle={`${result.original_filename ?? "uploaded file"} — ${
              result.duration_ms ?? "?"
            } ms`}
            lowContrast
            hideCloseButton
          />
          <Tile style={{ marginTop: "1rem", maxWidth: "640px" }}>
            <h3 style={{ marginTop: 0, fontSize: "1rem" }}>Stats</h3>
            <CodeSnippet type="multi" feedback="Copied">
              {JSON.stringify(result.stats, null, 2)}
            </CodeSnippet>
            {result.warnings && result.warnings.length > 0 && (
              <>
                <h3 style={{ fontSize: "1rem" }}>Warnings</h3>
                <CodeSnippet type="multi" feedback="Copied">
                  {JSON.stringify(result.warnings, null, 2)}
                </CodeSnippet>
              </>
            )}
            <h3
              style={{
                fontSize: "1rem",
                marginTop: "1.25rem",
                marginBottom: "0.5rem",
              }}
            >
              Next steps
            </h3>
            <ButtonSet style={{ gap: "0.5rem", flexWrap: "wrap" }}>
              <Link href="/files" style={{ textDecoration: "none" }}>
                <Button kind="primary" size="sm" renderIcon={Folder}>
                  View in File Explorer
                </Button>
              </Link>
              {result.id && (
                <>
                  <Link
                    href={`/lineage?node_id=${encodeURIComponent(
                      result.id,
                    )}&direction=upstream`}
                    style={{ textDecoration: "none" }}
                  >
                    <Button kind="tertiary" size="sm" renderIcon={ArrowRight}>
                      Trace upstream
                    </Button>
                  </Link>
                  <Link
                    href={`/lineage?node_id=${encodeURIComponent(
                      result.id,
                    )}&direction=downstream`}
                    style={{ textDecoration: "none" }}
                  >
                    <Button kind="ghost" size="sm" renderIcon={ArrowRight}>
                      Trace downstream
                    </Button>
                  </Link>
                </>
              )}
            </ButtonSet>
          </Tile>
        </>
      )}

      {mode === "multi" && multiResult && (
        <MultiResultPanel result={multiResult} />
      )}

      {mode === "auto" && autoResult && <AutoBatchResultPanel result={autoResult} />}
    </>
  );
}

function AutoBatchResultPanel({ result }: { result: AutoBatchResponse }) {
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
        title={`Mixed batch ${result.status}`}
        subtitle={`${result.files.length} files dispatched · batch ${result.batch_uuid}`}
        lowContrast
        hideCloseButton
      />

      {result.project && (
        <Tile>
          <h3 style={{ marginTop: 0, fontSize: "1rem" }}>Project</h3>
          {result.project.error ? (
            <InlineNotification
              kind="warning"
              title="Project grouping failed"
              subtitle={result.project.error}
              lowContrast
              hideCloseButton
            />
          ) : (
            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
              <strong>{result.project.name}</strong>
              <Tag type="purple" size="sm">
                {result.project.attached_file_count} file(s) attached
              </Tag>
              {result.project.id && (
                <Link
                  href={`/files?project=${encodeURIComponent(result.project.id)}`}
                  style={{ textDecoration: "none", marginLeft: "auto" }}
                >
                  <Button kind="ghost" size="sm" renderIcon={Folder}>
                    Open project
                  </Button>
                </Link>
              )}
            </div>
          )}
        </Tile>
      )}

      <Tile>
        <h3 style={{ marginTop: 0, fontSize: "1rem" }}>Summary by parser</h3>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
          {Object.entries(result.summary).map(([source, count]) => (
            <Tag
              key={source}
              type={
                (SOURCE_TAG_TYPE as any)[source] ??
                (source === "unsupported" ? "red" : "cool-gray")
              }
              size="md"
            >
              {source} × {count}
            </Tag>
          ))}
        </div>
      </Tile>

      {result.cross_file_analysis && result.cross_file_analysis.length > 0 && (
        <Tile>
          <h3 style={{ marginTop: 0, fontSize: "1rem" }}>
            Cross-file connections detected
          </h3>
          {result.cross_file_analysis.map((entry) => (
            <div key={entry.source_type} style={{ marginBottom: "0.75rem" }}>
              <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                <Tag
                  type={(SOURCE_TAG_TYPE as any)[entry.source_type] ?? "cool-gray"}
                  size="sm"
                >
                  {entry.source_type}
                </Tag>
                <strong>
                  {entry.cross_file_follows.length} cross-file FOLLOWS edge(s)
                </strong>
                {entry.shared_entity_types.length > 0 && (
                  <span
                    style={{
                      fontSize: "0.85rem",
                      color: "var(--cds-text-secondary)",
                    }}
                  >
                    · shared:{" "}
                    {entry.shared_entity_types.join(", ")}
                  </span>
                )}
              </div>
              {entry.cross_file_follows.length > 0 && (
                <ul
                  style={{
                    margin: "0.25rem 0 0 1rem",
                    fontSize: "0.8rem",
                    fontFamily: "monospace",
                  }}
                >
                  {entry.cross_file_follows.slice(0, 5).map((cf, i) => (
                    <li key={i}>
                      {cf.from_job_qualified} → {cf.to_job_qualified}
                      {cf.condition ? ` (${cf.condition})` : ""}
                    </li>
                  ))}
                  {entry.cross_file_follows.length > 5 && (
                    <li>
                      … and {entry.cross_file_follows.length - 5} more
                    </li>
                  )}
                </ul>
              )}
            </div>
          ))}
          <small style={{ color: "var(--cds-text-secondary)" }}>
            These edges live in Neo4j and will render automatically in the
            explorer's combined-lineage view.
          </small>
        </Tile>
      )}

      <Tile>
        <h3 style={{ marginTop: 0, fontSize: "1rem" }}>Per-file results</h3>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid #e0e0e0" }}>
              <th style={{ padding: "0.4rem 0.6rem" }}>File</th>
              <th style={{ padding: "0.4rem 0.6rem" }}>Detected</th>
              <th style={{ padding: "0.4rem 0.6rem" }}>Status</th>
              <th style={{ padding: "0.4rem 0.6rem", textAlign: "right" }}>
                Duration
              </th>
              <th style={{ padding: "0.4rem 0.6rem" }}>Detail</th>
            </tr>
          </thead>
          <tbody>
            {result.files.map((f) => (
              <tr
                key={f.uploaded_as}
                style={{ borderBottom: "1px solid #f4f4f4" }}
              >
                <td style={{ padding: "0.4rem 0.6rem", fontFamily: "monospace" }}>
                  {f.original_filename}
                </td>
                <td style={{ padding: "0.4rem 0.6rem" }}>
                  {f.source_type ? (
                    <Tag
                      type={(SOURCE_TAG_TYPE as any)[f.source_type] ?? "cool-gray"}
                      size="sm"
                    >
                      {f.source_type}
                    </Tag>
                  ) : (
                    <Tag type="red" size="sm">
                      —
                    </Tag>
                  )}
                </td>
                <td style={{ padding: "0.4rem 0.6rem" }}>
                  <Tag
                    type={
                      f.status === "ok"
                        ? "green"
                        : f.status === "partial"
                        ? "warm-gray"
                        : "red"
                    }
                    size="sm"
                  >
                    {f.status}
                  </Tag>
                </td>
                <td style={{ padding: "0.4rem 0.6rem", textAlign: "right" }}>
                  {f.duration_ms != null ? `${f.duration_ms} ms` : "—"}
                </td>
                <td
                  style={{
                    padding: "0.4rem 0.6rem",
                    fontSize: "0.75rem",
                    color: "var(--cds-text-secondary)",
                    maxWidth: "20rem",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                  title={f.detail ?? ""}
                >
                  {f.detail ??
                    (f.stats
                      ? `${Object.keys(f.stats).length} stat fields`
                      : "—")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Tile>

      <Tile>
        <h3 style={{ marginTop: 0, fontSize: "1rem" }}>Next step</h3>
        <Link href="/files" style={{ textDecoration: "none" }}>
          <Button kind="primary" renderIcon={Folder}>
            View parsed files
          </Button>
        </Link>
      </Tile>
    </Stack>
  );
}
