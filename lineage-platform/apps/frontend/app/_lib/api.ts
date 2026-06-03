/**
 * Typed gateway client. The frontend never talks to Neo4j or parsers directly —
 * everything goes through the FastAPI gateway.
 */

const GATEWAY = process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8000";

export type SourceSystem =
  | "tableau"
  | "qlikview"
  | "tws"
  | "spark"
  | "shared"
  | "unknown";

export interface GraphNode {
  data: {
    id: string;
    label: string;
    labels?: string[];
    source_system?: SourceSystem;
    properties?: Record<string, unknown>;
  };
}

export interface GraphEdge {
  data: {
    id: string;
    source: string;
    target: string;
    label: string;
    properties?: Record<string, unknown>;
  };
}

export interface GraphPayload {
  nodes: GraphNode[];
  edges: GraphEdge[];
  rows?: Record<string, unknown>[];
}

export interface TwsJob {
  job_name: string;
  workstation: string;
  start_time?: string;
  end_time?: string;
  script_path?: string;
  schedule_name?: string;
}

export interface ParseResponse {
  id: string | null;
  source_type: string;
  stats: Record<string, number>;
  duration_ms?: number;
  warnings?: { type: string; detail: string; line?: number | null }[];
  // TWS files produce multiple top-level nodes (one per Schedule); the
  // parser returns id=null and lists the schedule node ids here. Other
  // parsers leave this unset and use id.
  parsed_node_ids?: string[];
}

// Multi-file parse — used by /parse/multi. Per-file results + a
// commonality report listing shared entities and cross-file FOLLOWS.

export interface PerFileResult {
  file_path: string;
  status: "ok" | "partial" | "failed";
  parsed_schedules: number;
  parsed_jobs: number;
  parse_errors: number;
  warnings: { type: string; detail: string; line?: number | null }[];
}

export interface SharedEntity {
  id: string;
  name: string;
  label: string;
  source_files: string[];
}

export interface CrossFileFollows {
  from_file: string;
  from_job_qualified: string;
  to_file: string;
  to_job_qualified: string;
  condition: string | null;
}

export interface CommonalityReport {
  shared_entities: Record<string, SharedEntity[]>;
  file_specific: Record<string, Record<string, string[]>>;
  cross_file_follows: CrossFileFollows[];
}

export interface MultiParseResponse {
  status: "ok" | "partial" | "failed";
  files: PerFileResult[];
  merged_stats: Record<string, number>;
  commonality: CommonalityReport;
  duration_ms: number;
  warnings: { type: string; detail: string; line?: number | null }[];
  batch_uuid?: string;
  uploaded_as?: string[];
}

// Heterogeneous batch upload — N files of any types, each routed to its
// parser based on the file extension.
export interface AutoBatchFileResult {
  original_filename: string;
  uploaded_as: string;
  source_type: string | null;       // null = unsupported suffix
  status: "ok" | "partial" | "failed" | "unsupported" | "parser_unreachable";
  parsed_id?: string;
  detail?: string;
  http_status?: number;
  stats?: Record<string, number>;
  duration_ms?: number;
  warnings?: { type: string; detail: string; line?: number | null }[];
}

export interface AutoBatchProjectPayload {
  id?: string;
  name?: string;
  attached_file_count?: number;
  requested_name?: string;
  error?: string;
  status_code?: number;
}

export interface CrossFileAnalysisEntry {
  source_type: string;
  shared_entity_types: string[];
  cross_file_follows: CrossFileFollows[];
  merged_stats: Record<string, number>;
}

export interface AutoBatchResponse {
  status: "ok" | "partial" | "failed";
  batch_uuid: string;
  files: AutoBatchFileResult[];
  summary: Record<string, number>;
  project?: AutoBatchProjectPayload | null;
  cross_file_analysis?: CrossFileAnalysisEntry[];
}

// Projects — user-named groupings of parsed files.
export interface ProjectSummary {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  file_count: number;
  by_source: Record<string, number>;
}

export interface ProjectFile {
  neo4j_id: string;
  source_type: string;
  file_name: string | null;
  added_at: string;
}

export interface ProjectDetail {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  files: ProjectFile[];
  by_source: Record<string, ProjectFile[]>;
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${GATEWAY}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`GET ${path} → ${r.status}: ${await r.text()}`);
  return r.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${GATEWAY}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`POST ${path} → ${r.status}: ${await r.text()}`);
  return r.json();
}

export interface FileEntry {
  id: string;
  name: string;
  file_path?: string | null;
  type: string;
  version?: string | null;
  script_type?: string | null;
  workstation?: string | null;
  scheduler?: string | null;
  parsed_at?: string | null;
}

export type FilesIndex = Record<string, FileEntry[]>;

export interface FileSourceResponse {
  file_id: string;
  source: string;
  file_path: string;
  name: string | null;
  language: string;
  size_bytes: number;
  truncated: boolean;
  line_count: number;
  source_code: string;
}

export const api = {
  health: () =>
    get<{ status: string; neo4j: string; postgres: string }>("/health"),

  schema: () =>
    get<{ labels: string[]; relationship_types: string[]; property_keys: string[] }>(
      "/graph/schema",
    ),

  listNodes: (params: {
    label?: string;
    name_like?: string;
    limit?: number;
    offset?: number;
  }) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== "") qs.set(k, String(v));
    });
    return get<GraphPayload>(`/graph/nodes?${qs.toString()}`);
  },

  neighbors: (nodeId: string, depth = 1) =>
    get<GraphPayload>(
      `/graph/node/${encodeURIComponent(nodeId)}/neighbors?depth=${depth}`,
    ),

  preset: (name: string, nodeId?: string) => {
    const qs = new URLSearchParams();
    if (nodeId) qs.set("node_id", nodeId);
    return post<GraphPayload>(
      `/graph/query/preset/${encodeURIComponent(name)}?${qs.toString()}`,
      {},
    );
  },

  presets: () => get<{ presets: string[] }>("/graph/query/presets"),

  fileSource: (source: string, fileId: string) =>
    get<FileSourceResponse>(
      `/files/${encodeURIComponent(source)}/${encodeURIComponent(fileId)}/source`,
    ),

  cypher: (cypher: string, parameters: Record<string, unknown> = {}) =>
    post<GraphPayload>("/graph/query/cypher", { cypher, parameters }),

  twsJobs: (params: {
    start_time?: string;
    end_time?: string;
    script_path_like?: string;
    workstation?: string;
    limit?: number;
  }) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== "") qs.set(k, String(v));
    });
    return get<{ rows: TwsJob[]; count: number }>(`/tws/jobs?${qs.toString()}`);
  },

  parse: (source_type: string, file_path: string, overwrite = false) =>
    post<ParseResponse>("/parse", { source_type, file_path, overwrite }),

  parseUpload: async (
    source_type: string,
    file: File,
    overwrite = false,
  ): Promise<ParseResponse & { uploaded_as?: string; original_filename?: string }> => {
    const fd = new FormData();
    fd.append("source_type", source_type);
    fd.append("overwrite", String(overwrite));
    fd.append("file", file);
    const r = await fetch(`${GATEWAY}/parse/upload`, {
      method: "POST",
      body: fd,
    });
    if (!r.ok) {
      throw new Error(`POST /parse/upload → ${r.status}: ${await r.text()}`);
    }
    return r.json();
  },

  parseUploadMulti: async (
    source_type: string,
    files: File[],
    overwrite = false,
  ): Promise<MultiParseResponse> => {
    const fd = new FormData();
    fd.append("source_type", source_type);
    fd.append("overwrite", String(overwrite));
    for (const f of files) {
      fd.append("files", f);
    }
    const r = await fetch(`${GATEWAY}/parse/upload/multi`, {
      method: "POST",
      body: fd,
    });
    if (!r.ok) {
      throw new Error(
        `POST /parse/upload/multi → ${r.status}: ${await r.text()}`,
      );
    }
    return r.json();
  },

  parseUploadAuto: async (
    files: File[],
    overwrite = false,
    project_name?: string,
  ): Promise<AutoBatchResponse> => {
    const fd = new FormData();
    fd.append("overwrite", String(overwrite));
    for (const f of files) {
      fd.append("files", f);
    }
    if (project_name && project_name.trim()) {
      fd.append("project_name", project_name.trim());
    }
    const r = await fetch(`${GATEWAY}/parse/upload/auto`, {
      method: "POST",
      body: fd,
    });
    if (!r.ok) {
      throw new Error(
        `POST /parse/upload/auto → ${r.status}: ${await r.text()}`,
      );
    }
    return r.json();
  },

  listProjects: () => get<ProjectSummary[]>("/projects"),

  getProject: (id: string) =>
    get<ProjectDetail>(`/projects/${encodeURIComponent(id)}`),

  createProject: async (
    name: string,
    description?: string,
  ): Promise<{ id: string; name: string; description: string | null; created_at: string }> => {
    return post("/projects", { name, description: description ?? null });
  },

  deleteProject: async (id: string): Promise<{ deleted: boolean; id: string }> => {
    const r = await fetch(`${GATEWAY}/projects/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
    if (!r.ok)
      throw new Error(`DELETE /projects/${id} → ${r.status}: ${await r.text()}`);
    return r.json();
  },

  parserHealth: () =>
    get<Record<string, string>>("/parse/parsers/health"),

  files: () => get<FilesIndex>("/files"),
  filesSummary: () => get<Record<string, number>>("/files/summary"),

  deleteFile: async (
    source: string,
    fileId: string,
  ): Promise<{
    deleted: boolean;
    source: string;
    file_id: string;
    nodes_deleted: number;
  }> => {
    const r = await fetch(
      `${GATEWAY}/files/${encodeURIComponent(source)}/${encodeURIComponent(
        fileId,
      )}`,
      { method: "DELETE" },
    );
    if (!r.ok) {
      throw new Error(`DELETE /files → ${r.status}: ${await r.text()}`);
    }
    return r.json();
  },

  bulkDeleteFiles: async (
    items: { source: string; file_id: string }[],
  ): Promise<{
    requested: number;
    succeeded: number;
    failed: number;
    nodes_deleted: number;
    results: {
      source: string;
      file_id: string;
      deleted: boolean;
      nodes_deleted: number;
      error?: string;
    }[];
  }> => {
    const r = await fetch(`${GATEWAY}/files/bulk-delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ files: items }),
    });
    if (!r.ok) {
      throw new Error(`POST /files/bulk-delete → ${r.status}: ${await r.text()}`);
    }
    return r.json();
  },
};

export const GATEWAY_URL = GATEWAY;
