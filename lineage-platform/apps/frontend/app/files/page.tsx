"use client";

import {
  Button,
  Checkbox,
  InlineNotification,
  Loading,
  Modal,
  Tag,
  ToastNotification,
} from "@carbon/react";
import {
  ChartNetwork,
  Copy,
  Document,
  Folder,
  FolderShared,
  Grid as GridIcon,
  List as ListIcon,
  Renew,
  TrashCan,
  WatsonHealthDicomOverlay,
} from "@carbon/icons-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { PageHeader } from "../_components/PageHeader";
import {
  api,
  FileEntry,
  FilesIndex,
  ProjectDetail,
  ProjectSummary,
} from "../_lib/api";

const SOURCE_LABEL: Record<string, string> = {
  tableau: "Tableau",
  qlikview: "QlikView",
  tws: "TWS",
  spark: "Spark",
};

const SOURCE_ORDER: ("tableau" | "qlikview" | "tws" | "spark")[] = [
  "tableau",
  "qlikview",
  "tws",
  "spark",
];

const SOURCE_TAG_TYPE: Record<string, any> = {
  tableau: "blue",
  qlikview: "green",
  tws: "magenta",
  spark: "warm-gray",
};

interface SelectedItem {
  source: string;
  entry: FileEntry;
}

export default function FilesPage() {
  // useSearchParams needs a Suspense boundary at the page level under the
  // App Router's static-export model.
  return (
    <Suspense fallback={null}>
      <FilesPageInner />
    </Suspense>
  );
}

function FilesPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [index, setIndex] = useState<FilesIndex>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Active folder in the left pane. Initialised to the first source that
  // actually has files once the index loads.
  const [activeFolder, setActiveFolder] = useState<string | null>(null);
  const [selected, setSelected] = useState<SelectedItem | null>(null);

  // Projects sidebar + active project detail. When ``activeProject`` is
  // non-null, the middle pane shows the project view instead of the
  // source-folder file list.
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [activeProject, setActiveProject] = useState<ProjectDetail | null>(null);

  // Multi-select state — independent of the right-pane single ``selected``.
  // The Set holds FileEntry.id values (Neo4j node ids — globally unique
  // across all source types, so we don't need to compound with source).
  // Selection persists across folder switches so the user can pick e.g. a
  // Tableau workbook, a TWS schedule, and a Spark script together.
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  // Map id → FileEntry so the footer can show what's selected even when
  // the user has navigated to a different folder.
  const [checkedEntries, setCheckedEntries] = useState<Map<string, SelectedItem>>(
    new Map(),
  );

  // Delete-confirmation modal state
  const [confirmDelete, setConfirmDelete] = useState<SelectedItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteResult, setDeleteResult] = useState<
    { name: string; nodes: number } | null
  >(null);

  // Bulk-delete state — separate from the single-file flow so the two modals
  // can coexist and one's confirmation/loading doesn't interfere with the other.
  const [confirmBulkDelete, setConfirmBulkDelete] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [bulkDeleteResult, setBulkDeleteResult] = useState<
    { requested: number; succeeded: number; failed: number; nodes: number } | null
  >(null);

  function toggleChecked(source: string, entry: FileEntry) {
    setCheckedIds((prev) => {
      const next = new Set(prev);
      if (next.has(entry.id)) next.delete(entry.id);
      else next.add(entry.id);
      return next;
    });
    setCheckedEntries((prev) => {
      const next = new Map(prev);
      if (next.has(entry.id)) next.delete(entry.id);
      else next.set(entry.id, { source, entry });
      return next;
    });
  }

  function clearChecked() {
    setCheckedIds(new Set());
    setCheckedEntries(new Map());
  }

  // Batch select / deselect every item in a (source, items) group. Used by
  // the select-all checkbox in FileList headers and the project-view source
  // sub-section headers. Selection is global (persists across folder switches)
  // so callers pass their own source so the entries map stays consistent.
  function setManyChecked(
    source: string,
    entries: FileEntry[],
    checked: boolean,
  ) {
    if (entries.length === 0) return;
    setCheckedIds((prev) => {
      const next = new Set(prev);
      for (const e of entries) {
        if (checked) next.add(e.id);
        else next.delete(e.id);
      }
      return next;
    });
    setCheckedEntries((prev) => {
      const next = new Map(prev);
      for (const e of entries) {
        if (checked) next.set(e.id, { source, entry: e });
        else next.delete(e.id);
      }
      return next;
    });
  }

  function openCombinedLineage() {
    if (checkedIds.size === 0) return;
    const ids = Array.from(checkedIds).map(encodeURIComponent).join(",");
    router.push(`/lineage?node_ids=${ids}`);
  }

  async function performDelete() {
    if (!confirmDelete) return;
    setDeleting(true);
    setError(null);
    try {
      const r = await api.deleteFile(
        confirmDelete.source,
        confirmDelete.entry.id,
      );
      setDeleteResult({
        name: confirmDelete.entry.name,
        nodes: r.nodes_deleted,
      });
      // Drop selection if the deleted file was selected
      if (selected?.entry.id === confirmDelete.entry.id) {
        setSelected(null);
      }
      setConfirmDelete(null);
      await refresh();
    } catch (e: any) {
      setError(e?.message ?? String(e));
      setConfirmDelete(null);
    } finally {
      setDeleting(false);
    }
  }

  async function performBulkDelete() {
    if (checkedEntries.size === 0) return;
    setBulkDeleting(true);
    setError(null);
    const items = Array.from(checkedEntries.values()).map((s) => ({
      source: s.source,
      file_id: s.entry.id,
    }));
    const deletedIds = new Set(items.map((it) => it.file_id));
    try {
      const r = await api.bulkDeleteFiles(items);
      setBulkDeleteResult({
        requested: r.requested,
        succeeded: r.succeeded,
        failed: r.failed,
        nodes: r.nodes_deleted,
      });
      // Clear the multi-select for everything we just deleted.
      setCheckedIds((prev) => {
        const next = new Set(prev);
        for (const id of deletedIds) next.delete(id);
        return next;
      });
      setCheckedEntries((prev) => {
        const next = new Map(prev);
        for (const id of deletedIds) next.delete(id);
        return next;
      });
      // Drop the right-pane selection if it was one of the deleted files.
      if (selected && deletedIds.has(selected.entry.id)) {
        setSelected(null);
      }
      setConfirmBulkDelete(false);
      await refresh();
    } catch (e: any) {
      setError(e?.message ?? String(e));
      setConfirmBulkDelete(false);
    } finally {
      setBulkDeleting(false);
    }
  }

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const [data, projs] = await Promise.all([
        api.files(),
        api.listProjects().catch(() => [] as ProjectSummary[]),
      ]);
      // Strip per-source error entries (keys starting with _error_)
      const clean: FilesIndex = {};
      Object.entries(data).forEach(([k, v]) => {
        if (!k.startsWith("_error_")) clean[k] = v as FileEntry[];
      });
      setIndex(clean);
      setProjects(projs);
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setLoading(false);
    }
  }

  async function selectProject(id: string) {
    setSelected(null);
    setActiveFolder(null);
    try {
      const detail = await api.getProject(id);
      setActiveProject(detail);
    } catch (e: any) {
      const msg = e?.message ?? String(e);
      // Stale id (deleted out from under the sidebar) — refresh the list
      // silently instead of dumping a hard error on the user.
      if (msg.includes("404")) {
        setActiveProject(null);
        try {
          const fresh = await api.listProjects();
          setProjects(fresh);
        } catch {
          // ignore — primary `refresh()` will pick it up next mount
        }
        setError(
          "That project no longer exists — list refreshed. Pick another.",
        );
      } else {
        setError(msg);
        setActiveProject(null);
      }
    }
  }

  async function deleteActiveProject() {
    if (!activeProject) return;
    if (!window.confirm(
      `Delete project "${activeProject.name}"? Underlying files stay in the graph.`,
    )) return;
    try {
      await api.deleteProject(activeProject.id);
      setActiveProject(null);
      setActiveFolder(null);
      await refresh();
    } catch (e: any) {
      setError(e?.message ?? String(e));
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  // After an index refresh, default the active folder to the first source
  // that has anything in it. Keeps the user's prior choice if it still has
  // files; otherwise picks the first non-empty source.
  useEffect(() => {
    if (loading) return;
    if (activeFolder && (index[activeFolder]?.length ?? 0) > 0) return;
    const first = SOURCE_ORDER.find((s) => (index[s]?.length ?? 0) > 0);
    setActiveFolder(first ?? SOURCE_ORDER[0] ?? null);
  }, [index, loading, activeFolder]);

  // Honour ``?project=<id>`` deep links (used by the "Open project" CTA on
  // the parse result panel). Runs once after the projects list is loaded
  // so we only try to open a project that's actually known to exist.
  useEffect(() => {
    const deepLink = searchParams?.get("project");
    if (!deepLink) return;
    if (loading) return;
    if (projects.length === 0) return;
    // Only attempt if it's in the freshly loaded list — otherwise
    // selectProject would soft-fail and feel like noise.
    if (!projects.some((p) => p.id === deepLink)) return;
    if (activeProject?.id === deepLink) return;
    selectProject(deepLink);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, projects]);

  const totalCount = useMemo(
    () => Object.values(index).reduce((acc, arr) => acc + (arr?.length ?? 0), 0),
    [index],
  );

  return (
    <>
      <PageHeader
        title="Files"
        subtitle="Every source parsed into the lineage graph. Click a file to inspect, then trace its lineage."
        breadcrumbs={[
          { label: "Home", href: "/" },
          { label: "Files", current: true },
        ]}
        actions={
          <Button
            kind="ghost"
            renderIcon={Renew}
            onClick={refresh}
            disabled={loading}
          >
            Refresh
          </Button>
        }
      />

      {error && (
        <ToastNotification
          kind="error"
          title="Operation failed"
          subtitle={error}
          timeout={6000}
          onClose={() => setError(null)}
        />
      )}

      {deleteResult && (
        <ToastNotification
          kind="success"
          title="Deleted from graph"
          subtitle={`${deleteResult.name} — ${deleteResult.nodes} node${
            deleteResult.nodes === 1 ? "" : "s"
          } removed from Neo4j`}
          timeout={5000}
          onClose={() => setDeleteResult(null)}
        />
      )}

      {confirmDelete && (
        <Modal
          open
          danger
          modalHeading={`Delete "${confirmDelete.entry.name}"?`}
          primaryButtonText={deleting ? "Deleting…" : "Delete"}
          secondaryButtonText="Cancel"
          primaryButtonDisabled={deleting}
          onRequestClose={() => !deleting && setConfirmDelete(null)}
          onRequestSubmit={performDelete}
        >
          <p style={{ marginBottom: "0.75rem" }}>
            This removes the <strong>{SOURCE_LABEL[confirmDelete.source]}</strong>{" "}
            file node and every DataFrame / Worksheet / Job / Attribute it
            uniquely owns from Neo4j.
          </p>
          <InlineNotification
            kind="info"
            lowContrast
            hideCloseButton
            title="Shared nodes are kept"
            subtitle="Tables and Connections referenced by this file stay in the graph — other files may still depend on them."
            style={{ maxWidth: "none" }}
          />
        </Modal>
      )}

      {bulkDeleteResult && (
        <ToastNotification
          kind={bulkDeleteResult.failed === 0 ? "success" : "warning"}
          title={
            bulkDeleteResult.failed === 0
              ? "Files deleted"
              : "Some files weren't deleted"
          }
          subtitle={
            `${bulkDeleteResult.succeeded}/${bulkDeleteResult.requested} files removed` +
            ` — ${bulkDeleteResult.nodes} node${
              bulkDeleteResult.nodes === 1 ? "" : "s"
            } cleared from Neo4j` +
            (bulkDeleteResult.failed > 0
              ? ` (${bulkDeleteResult.failed} failed — check console for detail)`
              : "")
          }
          timeout={6000}
          onClose={() => setBulkDeleteResult(null)}
        />
      )}

      {confirmBulkDelete && (
        <Modal
          open
          danger
          modalHeading={`Delete ${checkedEntries.size} file${
            checkedEntries.size === 1 ? "" : "s"
          }?`}
          primaryButtonText={bulkDeleting ? "Deleting…" : "Delete all"}
          secondaryButtonText="Cancel"
          primaryButtonDisabled={bulkDeleting}
          onRequestClose={() => !bulkDeleting && setConfirmBulkDelete(false)}
          onRequestSubmit={performBulkDelete}
        >
          <p style={{ marginBottom: "0.75rem" }}>
            This removes the following files and every DataFrame /
            Worksheet / Job / Attribute they uniquely own from Neo4j.
          </p>
          <ul
            style={{
              maxHeight: "12rem",
              overflowY: "auto",
              border: "1px solid var(--cds-border-subtle-01, #e0e0e0)",
              borderRadius: "0.25rem",
              padding: "0.5rem 0.75rem",
              marginBottom: "0.75rem",
              fontSize: "0.8125rem",
              lineHeight: 1.5,
            }}
          >
            {Array.from(checkedEntries.values()).map((s) => (
              <li key={s.entry.id}>
                <Tag type="gray" size="sm" style={{ marginRight: "0.5rem" }}>
                  {SOURCE_LABEL[s.source] ?? s.source}
                </Tag>
                {s.entry.name}
              </li>
            ))}
          </ul>
          <InlineNotification
            kind="info"
            lowContrast
            hideCloseButton
            title="Shared nodes are kept"
            subtitle="Tables and Connections referenced by any of these files stay in the graph — other files may still depend on them."
            style={{ maxWidth: "none" }}
          />
        </Modal>
      )}

      <div
        style={{
          fontSize: "0.75rem",
          color: "var(--cds-text-secondary)",
          marginBottom: "0.75rem",
        }}
      >
        {loading ? (
          <Loading description="Loading" small withOverlay={false} />
        ) : (
          <>
            {totalCount} file{totalCount === 1 ? "" : "s"} indexed across{" "}
            {Object.values(index).filter((v) => v.length > 0).length} parser
            {Object.values(index).filter((v) => v.length > 0).length === 1
              ? ""
              : "s"}
          </>
        )}
      </div>

      <div className="files-layout files-layout--grid">
        {/* ============================= LEFT: folder list ====================== */}
        <nav className="files-folders" aria-label="Parser folders">
          {projects.length > 0 && (
            <>
              <div
                style={{
                  fontSize: "0.7rem",
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  color: "var(--cds-text-secondary)",
                  padding: "0.5rem 0.75rem 0.25rem",
                }}
              >
                Projects
              </div>
              {projects.map((p) => {
                const active = activeProject?.id === p.id;
                return (
                  <button
                    key={p.id}
                    type="button"
                    className={`files-folder ${
                      active ? "files-folder--active" : ""
                    }`}
                    onClick={() => selectProject(p.id)}
                    aria-pressed={active}
                  >
                    <span className="files-folder__icon">
                      <FolderShared size={18} />
                    </span>
                    <span className="files-folder__name">{p.name}</span>
                    <span className="files-folder__count">{p.file_count}</span>
                  </button>
                );
              })}
              <div
                style={{
                  fontSize: "0.7rem",
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  color: "var(--cds-text-secondary)",
                  padding: "0.75rem 0.75rem 0.25rem",
                }}
              >
                By source
              </div>
            </>
          )}
          {SOURCE_ORDER.map((source) => {
            const items = index[source] ?? [];
            const active = activeFolder === source;
            return (
              <button
                key={source}
                type="button"
                className={`files-folder ${
                  active && !activeProject ? "files-folder--active" : ""
                }`}
                onClick={() => {
                  setActiveFolder(source);
                  setActiveProject(null);   // exit project view if any
                  if (selected && selected.source !== source) {
                    setSelected(null);
                  }
                }}
                aria-pressed={active && !activeProject}
              >
                <span className="files-folder__icon">
                  <Folder size={18} />
                </span>
                <span className="files-folder__name">
                  {SOURCE_LABEL[source]}
                </span>
                <span className="files-folder__count">{items.length}</span>
              </button>
            );
          })}
        </nav>

        {/* ============================= MIDDLE: file list ====================== */}
        <section className="files-list" aria-label="Files in folder">
          {activeProject ? (
            <ProjectView
              project={activeProject}
              checkedIds={checkedIds}
              onToggleChecked={(source, entry) =>
                toggleChecked(source, entry)
              }
              onSetSourceChecked={(source, entries, checked) =>
                setManyChecked(source, entries, checked)
              }
              onOpenWholeProject={() => {
                const ids = activeProject.files.map((f) => f.neo4j_id);
                router.push(
                  `/lineage?node_ids=${ids
                    .map(encodeURIComponent)
                    .join(",")}`,
                );
              }}
              onDeleteProject={deleteActiveProject}
            />
          ) : activeFolder ? (
            <FileList
              source={activeFolder}
              items={index[activeFolder] ?? []}
              selectedId={
                selected?.source === activeFolder
                  ? selected.entry.id
                  : null
              }
              checkedIds={checkedIds}
              onToggleChecked={(entry) =>
                toggleChecked(activeFolder, entry)
              }
              onSetAllChecked={(checked) =>
                setManyChecked(activeFolder, index[activeFolder] ?? [], checked)
              }
              onSelect={(entry) =>
                setSelected({ source: activeFolder, entry })
              }
              onRequestDelete={(entry) =>
                setConfirmDelete({ source: activeFolder, entry })
              }
            />
          ) : (
            <div className="files-detail__empty">
              <div>
                <p>No folder selected.</p>
              </div>
            </div>
          )}
        </section>

        {/* ============================= RIGHT: detail ========================== */}
        <section className="files-detail" aria-label="File detail">
          {selected ? (
            <FileDetail
              item={selected}
              onRequestDelete={(item) => setConfirmDelete(item)}
            />
          ) : (
            <div className="files-detail__empty">
              <div>
                <WatsonHealthDicomOverlay
                  size={32}
                  style={{ marginBottom: "1rem", opacity: 0.6 }}
                />
                <p>Pick a file from the list to see its details.</p>
                <p style={{ fontSize: "0.75rem", marginTop: "0.5rem" }}>
                  Each row is the top-level node a parser produced — no source
                  code is stored, only the file's identity.
                </p>
              </div>
            </div>
          )}
        </section>
      </div>

      {checkedIds.size > 0 && (
        <MultiSelectFooter
          selected={Array.from(checkedEntries.values())}
          onClear={clearChecked}
          onOpenLineage={openCombinedLineage}
          onDeleteSelected={() => setConfirmBulkDelete(true)}
        />
      )}
    </>
  );
}

function ProjectView({
  project,
  checkedIds,
  onToggleChecked,
  onSetSourceChecked,
  onOpenWholeProject,
  onDeleteProject,
}: {
  project: ProjectDetail;
  checkedIds: Set<string>;
  onToggleChecked: (source: string, entry: FileEntry) => void;
  onSetSourceChecked: (
    source: string, entries: FileEntry[], checked: boolean,
  ) => void;
  onOpenWholeProject: () => void;
  onDeleteProject: () => void;
}) {
  // Auto-create the per-parser subfolder structure: every parser with files
  // in this project gets its own collapsible section. Source types with
  // zero files don't appear so the view stays uncluttered.
  const subSections = (
    ["tableau", "tws", "qlikview", "spark"] as const
  ).filter((src) => (project.by_source[src]?.length ?? 0) > 0);

  return (
    <div className="files-list__header-wrap" style={{ overflow: "auto" }}>
      <header className="files-list__header">
        <Tag type="purple">{project.name}</Tag>
        <span className="files-list__count">
          {project.files.length} file{project.files.length === 1 ? "" : "s"}
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: "0.5rem" }}>
          <Button
            kind="primary"
            size="sm"
            renderIcon={ChartNetwork}
            onClick={onOpenWholeProject}
            disabled={project.files.length === 0}
            title="Open lineage for every file in this project"
          >
            Lineage: whole project
          </Button>
          <Button
            kind="danger--ghost"
            size="sm"
            hasIconOnly
            renderIcon={TrashCan}
            iconDescription="Delete project"
            tooltipPosition="left"
            onClick={onDeleteProject}
          />
        </div>
      </header>

      {project.description && (
        <p
          style={{
            padding: "0 0.75rem 0.5rem",
            color: "var(--cds-text-secondary)",
            fontSize: "0.875rem",
          }}
        >
          {project.description}
        </p>
      )}

      <div
        style={{
          padding: "0.5rem 0.75rem",
          fontSize: "0.8rem",
          color: "var(--cds-text-secondary)",
        }}
      >
        Tick files below for a subset-lineage view, or click a single file
        for its own detail panel. The "Lineage: whole project" button above
        seeds the explorer with every file in this project at once.
      </div>

      {subSections.map((source) => {
        const files = project.by_source[source] ?? [];
        const entries: FileEntry[] = files.map((f) => ({
          id: f.neo4j_id,
          name: f.file_name ?? f.neo4j_id,
          type: source,
        }));
        const checkedHere = entries.filter((e) => checkedIds.has(e.id)).length;
        const allChecked = entries.length > 0 && checkedHere === entries.length;
        const someChecked = checkedHere > 0 && checkedHere < entries.length;
        return (
          <div key={source} style={{ marginBottom: "0.75rem" }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.5rem",
                padding: "0.5rem 0.75rem",
                background: "var(--cds-layer-02, #e8e8e8)",
                fontSize: "0.85rem",
                fontWeight: 600,
              }}
            >
              <span
                onClick={(e) => e.stopPropagation()}
                style={{ display: "inline-flex" }}
                title={
                  allChecked
                    ? "Deselect all in this source"
                    : someChecked
                      ? `${checkedHere}/${entries.length} selected — click to select all`
                      : "Select all in this source"
                }
              >
                <Checkbox
                  id={`projchk-all-${source}`}
                  labelText=""
                  hideLabel
                  checked={allChecked}
                  indeterminate={someChecked}
                  onChange={(_: any, { checked }: any) =>
                    onSetSourceChecked(source, entries, !!checked)
                  }
                />
              </span>
              <Folder size={14} />
              <span style={{ textTransform: "capitalize" }}>{source}</span>
              <Tag type={SOURCE_TAG_TYPE[source] ?? "cool-gray"} size="sm">
                {files.length}
              </Tag>
              {checkedHere > 0 && (
                <span style={{ fontWeight: 400, opacity: 0.7, fontSize: "0.75rem" }}>
                  ({checkedHere} selected)
                </span>
              )}
            </div>
            <ul className="files-list__items" style={{ marginTop: 0 }}>
              {files.map((f) => {
                const entry: FileEntry = {
                  id: f.neo4j_id,
                  name: f.file_name ?? f.neo4j_id,
                  type: source,
                };
                const isChecked = checkedIds.has(f.neo4j_id);
                return (
                  <li
                    key={f.neo4j_id}
                    className="files-list__row"
                    title={f.file_name ?? f.neo4j_id}
                  >
                    <span
                      onClick={(e) => e.stopPropagation()}
                      style={{ display: "inline-flex", marginRight: "0.5rem" }}
                    >
                      <Checkbox
                        id={`projchk-${f.neo4j_id}`}
                        labelText=""
                        hideLabel
                        checked={isChecked}
                        onChange={() => onToggleChecked(source, entry)}
                      />
                    </span>
                    <Document size={16} className="files-list__row-icon" />
                    <span className="files-list__row-text">
                      <span className="files-list__row-name">
                        {f.file_name ?? f.neo4j_id}
                      </span>
                      <span className="files-list__row-path">
                        {f.neo4j_id}
                      </span>
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}

      {subSections.length === 0 && (
        <div style={{ padding: "1rem", color: "var(--cds-text-secondary)" }}>
          This project has no files yet. Upload mixed files in{" "}
          <Link href="/parse">Parse a source</Link> and group them into this
          project.
        </div>
      )}
    </div>
  );
}

function MultiSelectFooter({
  selected,
  onClear,
  onOpenLineage,
  onDeleteSelected,
}: {
  selected: SelectedItem[];
  onClear: () => void;
  onOpenLineage: () => void;
  onDeleteSelected: () => void;
}) {
  return (
    <div
      style={{
        position: "fixed",
        left: "50%",
        bottom: "1.5rem",
        transform: "translateX(-50%)",
        background: "var(--cds-layer-01, #f4f4f4)",
        border: "1px solid var(--cds-border-subtle-01, #e0e0e0)",
        borderRadius: "0.25rem",
        padding: "0.75rem 1rem",
        boxShadow: "0 4px 12px rgba(0,0,0,0.12)",
        display: "flex",
        gap: "0.75rem",
        alignItems: "center",
        zIndex: 1000,
        maxWidth: "calc(100vw - 2rem)",
      }}
    >
      <strong>
        {selected.length} file{selected.length === 1 ? "" : "s"} selected
      </strong>
      <span
        style={{
          fontSize: "0.75rem",
          color: "var(--cds-text-secondary)",
          maxWidth: "26rem",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={selected.map((s) => s.entry.name).join(", ")}
      >
        {selected.map((s) => s.entry.name).join(", ")}
      </span>
      <Button kind="ghost" size="sm" onClick={onClear}>
        Clear
      </Button>
      <Button
        kind="danger--ghost"
        size="sm"
        renderIcon={TrashCan}
        onClick={onDeleteSelected}
        disabled={selected.length === 0}
      >
        Delete selected
      </Button>
      <Button
        kind="primary"
        size="sm"
        renderIcon={ChartNetwork}
        onClick={onOpenLineage}
        disabled={selected.length === 0}
      >
        Open combined lineage
      </Button>
    </div>
  );
}

type ViewMode = "list" | "grid";
const VIEW_MODE_STORAGE_KEY = "files.viewMode";

function FileList({
  source,
  items,
  selectedId,
  checkedIds,
  onToggleChecked,
  onSetAllChecked,
  onSelect,
  onRequestDelete,
}: {
  source: string;
  items: FileEntry[];
  selectedId: string | null;
  checkedIds: Set<string>;
  onToggleChecked: (entry: FileEntry) => void;
  onSetAllChecked: (checked: boolean) => void;
  onSelect: (entry: FileEntry) => void;
  onRequestDelete: (entry: FileEntry) => void;
}) {
  // Persist the user's last-picked view across page navigations and reloads.
  // Default to ``list`` for users who haven't picked yet — it packs more rows
  // into the middle column on narrow screens.
  const [viewMode, setViewMode] = useState<ViewMode>("list");
  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem(VIEW_MODE_STORAGE_KEY);
    if (stored === "grid" || stored === "list") setViewMode(stored);
  }, []);
  const switchView = (next: ViewMode) => {
    setViewMode(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(VIEW_MODE_STORAGE_KEY, next);
    }
  };

  if (items.length === 0) {
    return (
      <div className="files-list__empty">
        <Folder size={28} style={{ opacity: 0.5, marginBottom: "0.75rem" }} />
        <p>Nothing ingested in {SOURCE_LABEL[source]} yet.</p>
        <p style={{ fontSize: "0.75rem", marginTop: "0.25rem" }}>
          Upload a file from <strong>Parse</strong> to populate this folder.
        </p>
      </div>
    );
  }

  // Select-all state derived from current items + global checkedIds. The
  // checkbox is indeterminate when some-but-not-all visible rows are checked.
  const checkedHere = items.filter((e) => checkedIds.has(e.id)).length;
  const allChecked = items.length > 0 && checkedHere === items.length;
  const someChecked = checkedHere > 0 && checkedHere < items.length;

  return (
    <div className="files-list__header-wrap">
      <header className="files-list__header">
        <span
          onClick={(e) => e.stopPropagation()}
          style={{ display: "inline-flex", marginRight: "0.25rem" }}
          title={
            allChecked
              ? "Deselect all"
              : someChecked
                ? `${checkedHere}/${items.length} selected — click to select all`
                : "Select all in this folder"
          }
        >
          <Checkbox
            id={`chk-all-${source}`}
            labelText=""
            hideLabel
            checked={allChecked}
            indeterminate={someChecked}
            onChange={(_: any, { checked }: any) => onSetAllChecked(!!checked)}
          />
        </span>
        <Tag type={SOURCE_TAG_TYPE[source] ?? "cool-gray"}>
          {SOURCE_LABEL[source]}
        </Tag>
        <span className="files-list__count">
          {items.length} file{items.length === 1 ? "" : "s"}
          {checkedHere > 0 && (
            <span style={{ marginLeft: "0.5rem", opacity: 0.75 }}>
              ({checkedHere} selected)
            </span>
          )}
        </span>
        <div
          className="files-list__view-toggle"
          role="group"
          aria-label="View mode"
        >
          <button
            type="button"
            className={`files-list__view-btn ${
              viewMode === "list" ? "files-list__view-btn--active" : ""
            }`}
            onClick={() => switchView("list")}
            aria-pressed={viewMode === "list"}
            title="List view"
          >
            <ListIcon size={16} />
          </button>
          <button
            type="button"
            className={`files-list__view-btn ${
              viewMode === "grid" ? "files-list__view-btn--active" : ""
            }`}
            onClick={() => switchView("grid")}
            aria-pressed={viewMode === "grid"}
            title="Icon view"
          >
            <GridIcon size={16} />
          </button>
        </div>
      </header>

      {viewMode === "list" ? (
        <ul className="files-list__items">
          {items.map((entry) => {
            const active = entry.id === selectedId;
            const isChecked = checkedIds.has(entry.id);
            return (
              <li
                key={entry.id}
                className={`files-list__row ${
                  active ? "files-list__row--active" : ""
                }`}
                onClick={() => onSelect(entry)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(entry);
                  }
                }}
                title={entry.file_path ?? entry.name}
              >
                <span
                  onClick={(e) => e.stopPropagation()}
                  style={{ display: "inline-flex", marginRight: "0.5rem" }}
                >
                  <Checkbox
                    id={`chk-${entry.id}`}
                    labelText=""
                    hideLabel
                    checked={isChecked}
                    onChange={(_: any, { checked }: any) => {
                      void checked;
                      onToggleChecked(entry);
                    }}
                  />
                </span>
                <Document size={16} className="files-list__row-icon" />
                <span className="files-list__row-text">
                  <span className="files-list__row-name">{entry.name}</span>
                  {entry.file_path && entry.file_path !== entry.name && (
                    <span className="files-list__row-path">
                      {entry.file_path}
                    </span>
                  )}
                </span>
                <Tag
                  type="outline"
                  size="sm"
                  className="files-list__row-type"
                >
                  {entry.type}
                </Tag>
                <Button
                  kind="ghost"
                  size="sm"
                  hasIconOnly
                  renderIcon={TrashCan}
                  iconDescription="Delete"
                  tooltipPosition="left"
                  onClick={(e: any) => {
                    e.stopPropagation();
                    onRequestDelete(entry);
                  }}
                  className="files-list__row-delete"
                />
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="files-list__grid" role="grid">
          {items.map((entry) => {
            const active = entry.id === selectedId;
            const isChecked = checkedIds.has(entry.id);
            return (
              <div
                key={entry.id}
                className={`files-card ${
                  active ? "files-card--active" : ""
                }`}
                onClick={() => onSelect(entry)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(entry);
                  }
                }}
                title={entry.file_path ?? entry.name}
                style={
                  isChecked
                    ? { outline: "2px solid var(--cds-interactive, #0f62fe)" }
                    : undefined
                }
              >
                <span
                  onClick={(e) => e.stopPropagation()}
                  style={{ position: "absolute", top: 8, left: 8 }}
                >
                  <Checkbox
                    id={`chk-card-${entry.id}`}
                    labelText=""
                    hideLabel
                    checked={isChecked}
                    onChange={(_: any, { checked }: any) => {
                      void checked;
                      onToggleChecked(entry);
                    }}
                  />
                </span>
                <div className="files-card__icon">
                  <Document size={32} />
                </div>
                <div className="files-card__name">{entry.name}</div>
                <div className="files-card__meta">
                  <Tag type="outline" size="sm">
                    {entry.type}
                  </Tag>
                </div>
                <Button
                  kind="ghost"
                  size="sm"
                  hasIconOnly
                  renderIcon={TrashCan}
                  iconDescription="Delete"
                  tooltipPosition="left"
                  onClick={(e: any) => {
                    e.stopPropagation();
                    onRequestDelete(entry);
                  }}
                  className="files-card__delete"
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function FileDetail({
  item,
  onRequestDelete,
}: {
  item: SelectedItem;
  onRequestDelete: (item: SelectedItem) => void;
}) {
  const { source, entry } = item;
  const lineageUp = `/lineage?node_id=${encodeURIComponent(
    entry.id,
  )}&direction=upstream`;
  const lineageDown = `/lineage?node_id=${encodeURIComponent(
    entry.id,
  )}&direction=downstream`;
  const explorerLink = `/explorer`;

  return (
    <>
      <div style={{ marginBottom: "1.25rem" }}>
        <div style={{ marginBottom: "0.5rem" }}>
          <Tag type={SOURCE_TAG_TYPE[source] ?? "cool-gray"}>
            {SOURCE_LABEL[source]}
          </Tag>{" "}
          <Tag type="outline">{entry.type}</Tag>
        </div>
        <h2
          style={{
            margin: 0,
            fontSize: "1.5rem",
            fontWeight: 400,
            color: "var(--cds-text-primary)",
          }}
        >
          {entry.name}
        </h2>
      </div>

      <section style={{ marginBottom: "1.5rem" }}>
        <h4
          style={{
            fontSize: "0.75rem",
            textTransform: "uppercase",
            letterSpacing: "0.32px",
            color: "var(--cds-text-secondary)",
            margin: "0 0 0.5rem 0",
            fontWeight: 600,
          }}
        >
          Metadata
        </h4>
        <dl className="lineage-sidebar__kv">
          <dt>id</dt>
          <dd
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.5rem",
              fontFamily: "IBM Plex Mono, monospace",
              fontSize: "0.75rem",
              wordBreak: "break-all",
            }}
          >
            <span>{entry.id}</span>
            <Button
              kind="ghost"
              size="sm"
              renderIcon={Copy}
              hasIconOnly
              iconDescription="Copy id"
              onClick={() => navigator.clipboard?.writeText(entry.id)}
            />
          </dd>

          {entry.file_path && (
            <>
              <dt>file_path</dt>
              <dd style={{ wordBreak: "break-all" }}>{entry.file_path}</dd>
            </>
          )}
          {entry.script_type && (
            <>
              <dt>script_type</dt>
              <dd>{entry.script_type}</dd>
            </>
          )}
          {entry.version && (
            <>
              <dt>version</dt>
              <dd>{entry.version}</dd>
            </>
          )}
          {entry.workstation && (
            <>
              <dt>workstation</dt>
              <dd>{entry.workstation}</dd>
            </>
          )}
          {entry.scheduler && (
            <>
              <dt>scheduler</dt>
              <dd>{entry.scheduler}</dd>
            </>
          )}
          {entry.parsed_at && (
            <>
              <dt>parsed_at</dt>
              <dd>{entry.parsed_at}</dd>
            </>
          )}
        </dl>
      </section>

      <section>
        <h4
          style={{
            fontSize: "0.75rem",
            textTransform: "uppercase",
            letterSpacing: "0.32px",
            color: "var(--cds-text-secondary)",
            margin: "0 0 0.75rem 0",
            fontWeight: 600,
          }}
        >
          Trace lineage
        </h4>
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "0.5rem",
          }}
        >
          <Link href={lineageUp} style={{ textDecoration: "none" }}>
            <Button
              kind="primary"
              size="md"
              style={{ width: "200px", whiteSpace: "nowrap" }}
            >
              Trace upstream
            </Button>
          </Link>
          <Link href={lineageDown} style={{ textDecoration: "none" }}>
            <Button
              kind="secondary"
              size="md"
              style={{ width: "200px", whiteSpace: "nowrap" }}
            >
              Trace downstream
            </Button>
          </Link>
          <Link href={explorerLink} style={{ textDecoration: "none" }}>
            <Button
              kind="tertiary"
              size="md"
              style={{ width: "200px", whiteSpace: "nowrap" }}
            >
              Open Graph explorer
            </Button>
          </Link>
        </div>
        <p
          style={{
            fontSize: "0.75rem",
            color: "var(--cds-text-secondary)",
            marginTop: "0.75rem",
          }}
        >
          Trace buttons open the lineage page with this file pre-selected as the
          starting node.
        </p>
      </section>

      <section
        style={{
          marginTop: "2rem",
          paddingTop: "1.25rem",
          borderTop: "1px solid var(--cds-border-subtle-01)",
        }}
      >
        <h4
          style={{
            fontSize: "0.75rem",
            textTransform: "uppercase",
            letterSpacing: "0.32px",
            color: "var(--cds-text-error)",
            margin: "0 0 0.75rem 0",
            fontWeight: 600,
          }}
        >
          Danger zone
        </h4>
        <Button
          kind="danger"
          size="md"
          renderIcon={TrashCan}
          onClick={() => onRequestDelete(item)}
        >
          Delete from graph
        </Button>
        <p
          style={{
            fontSize: "0.75rem",
            color: "var(--cds-text-secondary)",
            marginTop: "0.5rem",
          }}
        >
          Removes this file and the nodes it uniquely owns from Neo4j. Shared
          Tables and Connections stay (other files may still depend on them).
        </p>
      </section>
    </>
  );
}
