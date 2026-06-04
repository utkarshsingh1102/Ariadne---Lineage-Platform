// Upstream traversal — walks outgoing edges from the starting node toward
// the sources its data ultimately comes from.
//
// PROVIDES_DATAFRAME / WRITES_TO_CONNECTION are intentionally OMITTED
// here: they're Connection ↔ DataFrame edges. Including them during an
// upstream walk lets the traversal cross a shared :Connection node and
// fan out into every OTHER script that happens to use the same connection
// — which is almost never what "show me what feeds my pipeline" means.
// The :Connection still appears in the trace (it's reached via
// CONNECTS_VIA from the Table) but the walk stops there.
//
// The relationship list includes both *containment* edges (a file contains
// datasources, a workbook contains worksheets, etc.) and *lineage* edges
// (READS_TABLE, DERIVES_FROM, etc.). That lets a file-level node thread
// through to physical tables in one query.
MATCH path = (start)-[
  :CONTAINS_DATASOURCE
  |CONTAINS_DASHBOARD
  |CONTAINS_WORKSHEET
  |CONTAINS_TABLE
  |CONTAINS_CHART
  |CONTAINS_SHEET
  |CONTAINS_DATAFRAME
  |CONTAINS_SCHEDULE
  |CONTAINS_JOB
  |CONTAINS_COMPONENT
  |HAS_PARAMETER
  |HAS_FIELD
  |HAS_COLUMN
  |USES_FIELD
  |USES_PARAMETER
  |USES_CONNECTION
  |READS_TABLE
  |WRITES_TABLE
  |LOADS_FROM_TABLE
  |LOADS_FROM_FILE
  |DERIVES_FROM
  |DERIVES_FROM_REF
  |DERIVES_FROM_DATAFRAME
  |FILTERS_BY
  |SORTS_BY
  |HAS_ZONE
  |CONTROLS_PARAMETER
  |FILTERS_VIA_ACTION
  |SETS_PARAMETER
  |HAS_GROUP
  |HAS_SET
  |HAS_BIN
  |HAS_HIERARCHY
  |HAS_LEVEL
  |CONNECTS_VIA
  |DISPLAYS_WORKSHEET
  |JOINS_WITH
  |CALLS_SCRIPT
  // Improvement-v2 — :TableauParameterScope, blends, cross-DS refs, CTE columns.
  |HAS_PARAMETER_SCOPE
  |HAS_BLEND
  |BLENDS_WITH
  |DERIVES_FROM_CROSS_DS
  |DERIVES_FROM_CTE_COLUMN
  // TWS v0.2 — script + resource renames + new topology edges.
  // EXECUTES replaces CALLS_SCRIPT (above kept for legacy graphs pending migration).
  |EXECUTES
  // v0.3 cross-parser orchestration stitch: :Script -> :SparkScript /
  // :QlikScript / :TableauWorkbook (the actual file the TWS wrapper invokes).
  |INVOKES_FILE
  |REQUIRES_RESOURCE
  |WAITS_FOR_FILE
  |WAITS_FOR_PROMPT
  |RUNS_ON
  |HOSTS_STREAM
  |RECOVERS_WITH
  |TRIGGERS
  |SCHEDULED_BY
  |DEPENDS_ON
  // QlikView v0.2 / Phase 3 — attribute-level edges (DataPlatform →
  // DataConnection → PhysicalSource → Dataset → Attribute) + Sense /
  // server-meta surfaces.
  |SOURCED_FROM
  |HAS_ATTRIBUTE
  |STORED_AS
  |MAPS_TO
  |JOINS
  |REFERENCES_FK
  |HAS_CONSTRAINT
  |FEEDS_OBJECT
*1..6]->(upstream)
WHERE start.id = $node_id
   OR start.fully_qualified_name = $node_id
   OR start.path = $node_id
RETURN path
LIMIT 500
