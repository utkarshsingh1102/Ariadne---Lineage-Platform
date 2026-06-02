/**
 * Hover-tooltip copy for the filter chips at the top of the lineage canvas.
 *
 * One entry per node label that the toolbar can surface. Definitions are
 * deliberately short (1-2 sentences) — a reader hovering a chip wants
 * "what is this and why does it matter for lineage", not the full spec.
 *
 * Add new entries here when introducing a new label so the toolbar
 * doesn't fall back to a generic message.
 */
export type NodeDefinition = {
  /** Canonical Neo4j label, e.g. ``TableauDatasource``. */
  label: string;
  /** Source system / parser the label belongs to (for the badge). */
  system: "tableau" | "qlikview" | "tws" | "spark" | "shared";
  /** 1-2 sentence description shown in the popover body. */
  summary: string;
};

export const NODE_DEFINITIONS: Record<string, NodeDefinition> = {
  // ---- Shared (cross-parser) -------------------------------------------
  Attribute: {
    label: "Attribute",
    system: "shared",
    summary:
      "A column or calculated field. The atomic unit of column-level lineage — every formula trace ultimately resolves into Attribute nodes.",
  },
  Table: {
    label: "Table",
    system: "shared",
    summary:
      "A physical database table, custom-SQL relation, or stored procedure. Shared across parsers — the same table read by Spark and written by Tableau collapses to one node.",
  },
  Connection: {
    label: "Connection",
    system: "shared",
    summary:
      "A database connection profile (postgres, snowflake, oracle, …). Identified by class+server+dbname so the same connection used by multiple sources dedupes.",
  },
  Parameter: {
    label: "Parameter",
    system: "shared",
    summary:
      "A workbook-level parameter — drives filter values and calc-field branching. Owned by TableauParameterScope when emitted from Tableau.",
  },

  // ---- Spark -----------------------------------------------------------
  DataFrame: {
    label: "DataFrame",
    system: "spark",
    summary:
      "An intermediate PySpark DataFrame in a job's execution graph. Identifies one named-or-anonymous result; DERIVES_FROM_DATAFRAME chains expose the full provenance.",
  },
  UDF: {
    label: "UDF",
    system: "spark",
    summary:
      "A user-defined Python function callable from Spark SQL or DataFrame APIs. The return type and whether it's a pandas_udf are surfaced as node extras.",
  },
  SparkScript: {
    label: "SparkScript",
    system: "spark",
    summary: "A Python file processed by the Spark parser.",
  },
  Script: {
    label: "Script",
    system: "spark",
    summary:
      "A script artefact (Spark, shell, or job) that produced this lineage. The script_type tag tells you which.",
  },

  // ---- Tableau ---------------------------------------------------------
  TableauWorkbook: {
    label: "TableauWorkbook",
    system: "tableau",
    summary:
      "The root of a .twb / .twbx file. Owns every datasource, worksheet, dashboard, and parameter inside.",
  },
  TableauDatasource: {
    label: "TableauDatasource",
    system: "tableau",
    summary:
      "One bound connection + table set with its calculated fields. A workbook has one per distinct source; flags include has_extract and is_federated.",
  },
  TableauWorksheet: {
    label: "TableauWorksheet",
    system: "tableau",
    summary:
      "A view that binds fields to shelves (rows, cols, color, filter, …) — what Tableau calls a sheet. Owns USES_FIELD edges.",
  },
  TableauDashboard: {
    label: "TableauDashboard",
    system: "tableau",
    summary:
      "A composition of worksheets and zones laid out on a canvas. Owns DashboardZone nodes and dashboard Actions.",
  },
  DashboardZone: {
    label: "DashboardZone",
    system: "tableau",
    summary:
      "One non-worksheet slot on a dashboard — a quick filter, parameter control, text/image/web region, or container.",
  },
  TableauGroup: {
    label: "TableauGroup",
    system: "tableau",
    summary:
      "Field-value recoding (City → Region 'Coastal'/'Inland'). Derives from one source field via DERIVES_FROM.",
  },
  TableauSet: {
    label: "TableauSet",
    system: "tableau",
    summary:
      "A filtered subset of a field's values defined by a membership condition. The condition_expr captures the rule.",
  },
  TableauBin: {
    label: "TableauBin",
    system: "tableau",
    summary:
      "Numeric binning of a measure into ordinal buckets. Carries the source field and a bin size (which itself may be a parameter).",
  },
  TableauHierarchy: {
    label: "TableauHierarchy",
    system: "tableau",
    summary:
      "An ordered drill-path of fields (Country → Region → City). Levels are preserved by ordinal via HAS_LEVEL edges.",
  },
  TableauParameterScope: {
    label: "TableauParameterScope",
    system: "tableau",
    summary:
      "Synthetic node representing the workbook's <datasource name='Parameters'> block. Owns every Parameter via HAS_PARAMETER without polluting the TableauDatasource label.",
  },
  WorksheetBlend: {
    label: "WorksheetBlend",
    system: "tableau",
    summary:
      "A <datasource-relationship> data blend declared in a worksheet — links a primary and secondary datasource on shared field(s). Distinct from an in-database join.",
  },

  // ---- QlikView --------------------------------------------------------
  QlikScript: {
    label: "QlikScript",
    system: "qlikview",
    summary:
      "The body of a QlikView load script. Holds LOAD statements that emit QlikTable nodes plus Subroutines and Variables.",
  },
  QlikTable: {
    label: "QlikTable",
    system: "qlikview",
    summary:
      "A QlikView internal table produced by a LOAD, RESIDENT, or CONCATENATE statement. The lineage walker resolves its source columns.",
  },
  QlikSheet: {
    label: "QlikSheet",
    system: "qlikview",
    summary: "A sheet inside a .qvw containing one or more QlikChart nodes.",
  },
  QlikChart: {
    label: "QlikChart",
    system: "qlikview",
    summary:
      "One visualisation (pivot, table, gauge, …) on a sheet. The chart_type tag distinguishes the form.",
  },
  Variable: {
    label: "Variable",
    system: "qlikview",
    summary:
      "A QlikView script variable defined via LET or SET. Scope (global or sub-local) surfaces as an extra.",
  },
  Subroutine: {
    label: "Subroutine",
    system: "qlikview",
    summary:
      "A QlikView SUB … ENDSUB block. CALLS_SCRIPT edges show which subs invoke other subs.",
  },

  // ---- TWS -------------------------------------------------------------
  Schedule: {
    label: "Schedule",
    system: "tws",
    summary:
      "A TWS schedule on a workstation. Owns Job nodes and may declare FileWatcher and Resource dependencies.",
  },
  Job: {
    label: "Job",
    system: "tws",
    summary:
      "A scheduled job inside a TWS schedule. Carries start time, priority, and workstation as extras.",
  },
  Resource: {
    label: "Resource",
    system: "tws",
    summary:
      "A TWS numeric resource (e.g. license slots) that jobs consume. The quantity field shows the pool size.",
  },
  FileWatcher: {
    label: "FileWatcher",
    system: "tws",
    summary:
      "A TWS file dependency — a path that must exist before the dependent job is allowed to run.",
  },
};

/** Definition lookup with a sensible fallback for unknown labels. */
export function getNodeDefinition(label: string): NodeDefinition {
  return (
    NODE_DEFINITIONS[label] ?? {
      label,
      system: "shared",
      summary:
        "No definition registered for this node type yet. Add one in app/_lib/node-definitions.ts so the chip stops falling back here.",
    }
  );
}
