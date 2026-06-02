"""All Cypher templates in one place. Every statement uses MERGE for idempotency."""

PARSER_NAME = "tableau-parser"

# --- Nodes ----------------------------------------------------------------------

MERGE_WORKBOOK = """
UNWIND $rows AS row
MERGE (w:TableauWorkbook {id: row.id})
SET w.name = row.name,
    w.file_path = row.file_path,
    w.version = row.version,
    w.parsed_at = row.parsed_at,
    w.source_system = 'tableau',
    w.line = row.line,
    w.line_end = row.line_end
"""

MERGE_DATASOURCE = """
UNWIND $rows AS row
MERGE (d:TableauDatasource {id: row.id})
SET d.name = row.name,
    d.caption = row.caption,
    d.is_federated = row.is_federated,
    d.has_extract = row.has_extract,
    d.workbook_id = row.workbook_id,
    d.line = row.line
"""

MERGE_CONNECTION = """
UNWIND $rows AS row
MERGE (c:Connection {id: row.id})
ON CREATE SET c.class = row.class,
              c.server = row.server,
              c.dbname = row.dbname,
              c.schema = row.schema,
              c.port = row.port,
              c.username = row.username,
              c.first_seen_by = $parser
"""

MERGE_TABLE = """
UNWIND $rows AS row
MERGE (t:Table {fully_qualified_name: row.fully_qualified_name})
ON CREATE SET t.id = row.id,
              t.name = row.name,
              t.schema = row.schema,
              t.database = row.database,
              t.source_type = row.source_type,
              t.first_seen_by = $parser
ON MATCH SET  t.source_type = coalesce(t.source_type, row.source_type)
// Improvement-v2 §3a — keep the first non-empty raw_sql we see. Custom
// SQL bodies are large; we don't overwrite if a later batch has none.
SET t.raw_sql = CASE WHEN row.raw_sql <> '' THEN row.raw_sql ELSE t.raw_sql END
"""

MERGE_ATTRIBUTE = """
UNWIND $rows AS row
MERGE (a:Attribute {id: row.id})
SET a.name = row.name,
    a.datatype = row.datatype,
    a.role = row.role,
    a.is_calculated = row.is_calculated,
    a.formula = row.formula,
    a.line = coalesce(a.line, row.line),
    a.default_aggregation = row.default_aggregation,
    a.ordinal = row.ordinal,
    a.precision = row.precision,
    a.scale = row.scale,
    a.contains_null = row.contains_null,
    a.value_aliases = row.value_aliases
"""

MERGE_WORKSHEET = """
UNWIND $rows AS row
MERGE (w:TableauWorksheet {id: row.id})
SET w.name = row.name, w.workbook_id = row.workbook_id,
    w.line = row.line, w.line_end = row.line_end
"""

MERGE_DASHBOARD = """
UNWIND $rows AS row
MERGE (d:TableauDashboard {id: row.id})
SET d.name = row.name, d.workbook_id = row.workbook_id,
    d.line = row.line, d.line_end = row.line_end
"""

MERGE_PARAMETER = """
UNWIND $rows AS row
MERGE (p:Parameter {id: row.id})
SET p.name = row.name,
    p.datatype = row.datatype,
    p.current_value = row.current_value,
    p.workbook_id = row.workbook_id,
    p.line = row.line
"""

MERGE_DASHBOARD_ZONE = """
UNWIND $rows AS row
MERGE (z:DashboardZone {id: row.id})
SET z.kind = row.kind,
    z.name = row.name,
    z.target_worksheet = row.target_worksheet,
    z.target_parameter = row.target_parameter,
    z.dashboard_id = row.dashboard_id,
    z.line = row.line
"""

MERGE_TABLEAU_GROUP = """
UNWIND $rows AS row
MERGE (g:TableauGroup {id: row.id})
SET g.name = row.name,
    g.datasource_id = row.datasource_id,
    g.line = row.line
"""

MERGE_TABLEAU_SET = """
UNWIND $rows AS row
MERGE (s:TableauSet {id: row.id})
SET s.name = row.name,
    s.datasource_id = row.datasource_id,
    s.condition_expr = row.condition_expr,
    s.line = row.line
"""

MERGE_TABLEAU_BIN = """
UNWIND $rows AS row
MERGE (b:TableauBin {id: row.id})
SET b.name = row.name,
    b.datasource_id = row.datasource_id,
    b.size = row.size,
    b.line = row.line
"""

MERGE_TABLEAU_HIERARCHY = """
UNWIND $rows AS row
MERGE (h:TableauHierarchy {id: row.id})
SET h.name = row.name,
    h.datasource_id = row.datasource_id,
    h.line = row.line
"""

# --- Relationships --------------------------------------------------------------

CONTAINS_DATASOURCE = """
UNWIND $rows AS row
MATCH (w:TableauWorkbook {id: row.workbook_id})
MATCH (d:TableauDatasource {id: row.datasource_id})
MERGE (w)-[:CONTAINS_DATASOURCE]->(d)
"""

CONNECTS_VIA = """
UNWIND $rows AS row
MATCH (d:TableauDatasource {id: row.datasource_id})
MATCH (c:Connection {id: row.connection_id})
MERGE (d)-[r:CONNECTS_VIA]->(c)
SET r.line = coalesce(r.line, row.line)
"""

READS_TABLE = """
UNWIND $rows AS row
MATCH (d:TableauDatasource {id: row.datasource_id})
MATCH (t:Table {fully_qualified_name: row.fully_qualified_name})
MERGE (d)-[r:READS_TABLE]->(t)
SET r.relation_type = row.relation_type,
    r.line = coalesce(r.line, row.line)
"""

HAS_FIELD = """
UNWIND $rows AS row
MATCH (d:TableauDatasource {id: row.datasource_id})
MATCH (a:Attribute {id: row.field_id})
MERGE (d)-[:HAS_FIELD]->(a)
"""

HAS_COLUMN = """
UNWIND $rows AS row
MATCH (t:Table {fully_qualified_name: row.fully_qualified_name})
MATCH (a:Attribute {id: row.field_id})
MERGE (t)-[:HAS_COLUMN]->(a)
"""

DERIVES_FROM = """
UNWIND $rows AS row
MATCH (src:Attribute {id: row.from_id})
MATCH (dst:Attribute {id: row.to_id})
MERGE (src)-[r:DERIVES_FROM]->(dst)
SET r.formula = row.formula,
    r.line = coalesce(r.line, row.line)
"""

# DERIVES_FROM_REF — token-level per-reference edge mirroring DERIVES_FROM
# but at the *occurrence* granularity. One row per (target, source, span)
# tuple. Keyed on char_start so the same source field referenced twice in
# the same formula produces two distinct edges. Queryable (each ref carries
# its own properties), unlike packing a list-of-maps as a single property.
DERIVES_FROM_REF = """
UNWIND $rows AS row
MATCH (src:Attribute {id: row.from_id})
MATCH (dst:Attribute {id: row.to_id})
MERGE (src)-[r:DERIVES_FROM_REF {char_start: row.char_start}]->(dst)
SET r.char_end = row.char_end,
    r.kind = row.kind,
    r.line = coalesce(r.line, row.line),
    r.datasource_name = row.datasource_name
"""

CONTAINS_WORKSHEET = """
UNWIND $rows AS row
MATCH (w:TableauWorkbook {id: row.workbook_id})
MATCH (s:TableauWorksheet {id: row.worksheet_id})
MERGE (w)-[:CONTAINS_WORKSHEET]->(s)
"""

CONTAINS_DASHBOARD = """
UNWIND $rows AS row
MATCH (w:TableauWorkbook {id: row.workbook_id})
MATCH (d:TableauDashboard {id: row.dashboard_id})
MERGE (w)-[:CONTAINS_DASHBOARD]->(d)
"""

USES_FIELD = """
UNWIND $rows AS row
MATCH (s:TableauWorksheet {id: row.worksheet_id})
MATCH (a:Attribute {id: row.field_id})
// Multi-shelf usages are NOT collapsed any more (plan §4) — the same field
// on Rows AND Color produces two distinct edges keyed by ``shelf``.
MERGE (s)-[r:USES_FIELD {shelf: row.shelf}]->(a)
SET r.aggregation = row.aggregation,
    r.line = coalesce(r.line, row.line)
"""

# FILTERS_BY — from TableauWorksheet OR TableauDatasource. The writer
# emits two separate batches using either Cypher template below. The
# relationship is uniquely keyed by ``filter_class + field_id`` so
# multiple filters on the same field (e.g. range + exclusion) don't
# collapse into a single edge.
FILTERS_BY_WORKSHEET = """
UNWIND $rows AS row
MATCH (s:TableauWorksheet {id: row.worksheet_id})
MATCH (a:Attribute {id: row.field_id})
MERGE (s)-[r:FILTERS_BY {filter_class: row.filter_class}]->(a)
SET r.expression = row.expression,
    r.line = coalesce(r.line, row.line)
"""

FILTERS_BY_DATASOURCE = """
UNWIND $rows AS row
MATCH (s:TableauDatasource {id: row.datasource_id})
MATCH (a:Attribute {id: row.field_id})
MERGE (s)-[r:FILTERS_BY {filter_class: row.filter_class}]->(a)
SET r.expression = row.expression,
    r.line = coalesce(r.line, row.line)
"""

SORTS_BY = """
UNWIND $rows AS row
MATCH (s:TableauWorksheet {id: row.worksheet_id})
MATCH (a:Attribute {id: row.field_id})
MERGE (s)-[r:SORTS_BY {direction: row.direction}]->(a)
SET r.line = coalesce(r.line, row.line)
"""

HAS_ZONE = """
UNWIND $rows AS row
MATCH (d:TableauDashboard {id: row.dashboard_id})
MATCH (z:DashboardZone {id: row.zone_id})
MERGE (d)-[r:HAS_ZONE]->(z)
SET r.line = coalesce(r.line, row.line)
"""

CONTROLS_PARAMETER = """
UNWIND $rows AS row
MATCH (z:DashboardZone {id: row.zone_id})
MATCH (p:Parameter {id: row.parameter_id})
MERGE (z)-[r:CONTROLS_PARAMETER]->(p)
SET r.line = coalesce(r.line, row.line)
"""

# Dashboard actions become Worksheet→Worksheet edges (filter / highlight),
# Worksheet→Parameter edges (parameter actions), or Worksheet→Worksheet
# (URL/go-to-sheet — target_sheets-only). The kind is stored on the edge
# so a single source/target pair can host multiple action types.
FILTERS_VIA_ACTION = """
UNWIND $rows AS row
MATCH (src:TableauWorksheet {id: row.source_worksheet_id})
MATCH (tgt:TableauWorksheet {id: row.target_worksheet_id})
MERGE (src)-[r:FILTERS_VIA_ACTION {kind: row.kind, action_id: row.action_id}]->(tgt)
SET r.fields = row.fields,
    r.line = coalesce(r.line, row.line)
"""

SETS_PARAMETER = """
UNWIND $rows AS row
MATCH (src:TableauWorksheet {id: row.source_worksheet_id})
MATCH (p:Parameter {id: row.parameter_id})
MERGE (src)-[r:SETS_PARAMETER {action_id: row.action_id}]->(p)
SET r.line = coalesce(r.line, row.line)
"""

# Derived-field families — HAS_GROUP/SET/BIN/HIERARCHY connect a
# datasource to each of its first-class derived nodes. Source-field
# membership reuses the existing DERIVES_FROM relationship (group/set/bin
# → Attribute) so the lineage closure walks through naturally. Hierarchy
# levels use HAS_LEVEL because their relationship is ordering, not
# derivation.
HAS_GROUP = """
UNWIND $rows AS row
MATCH (d:TableauDatasource {id: row.datasource_id})
MATCH (g:TableauGroup {id: row.group_id})
MERGE (d)-[:HAS_GROUP]->(g)
"""

HAS_SET = """
UNWIND $rows AS row
MATCH (d:TableauDatasource {id: row.datasource_id})
MATCH (s:TableauSet {id: row.set_id})
MERGE (d)-[:HAS_SET]->(s)
"""

HAS_BIN = """
UNWIND $rows AS row
MATCH (d:TableauDatasource {id: row.datasource_id})
MATCH (b:TableauBin {id: row.bin_id})
MERGE (d)-[:HAS_BIN]->(b)
"""

HAS_HIERARCHY = """
UNWIND $rows AS row
MATCH (d:TableauDatasource {id: row.datasource_id})
MATCH (h:TableauHierarchy {id: row.hierarchy_id})
MERGE (d)-[:HAS_HIERARCHY]->(h)
"""

# Derived field → source attribute. Reuses DERIVES_FROM but adds a tag so
# downstream tools can distinguish "calc derived from field" from "group
# derived from field". Kind = "group" | "set" | "bin".
DERIVES_FROM_DERIVED = """
UNWIND $rows AS row
MATCH (derived) WHERE derived.id = row.derived_id
  AND any(lbl IN labels(derived) WHERE lbl IN ['TableauGroup','TableauSet','TableauBin'])
MATCH (a:Attribute {id: row.field_id})
MERGE (derived)-[r:DERIVES_FROM {kind: row.kind}]->(a)
SET r.line = coalesce(r.line, row.line)
"""

HAS_LEVEL = """
UNWIND $rows AS row
MATCH (h:TableauHierarchy {id: row.hierarchy_id})
MATCH (a:Attribute {id: row.field_id})
MERGE (h)-[r:HAS_LEVEL {ordinal: row.ordinal}]->(a)
"""

DISPLAYS_WORKSHEET = """
UNWIND $rows AS row
MATCH (d:TableauDashboard {id: row.dashboard_id})
MATCH (s:TableauWorksheet {id: row.worksheet_id})
MERGE (d)-[:DISPLAYS_WORKSHEET]->(s)
"""

HAS_PARAMETER = """
UNWIND $rows AS row
MATCH (w:TableauWorkbook {id: row.workbook_id})
MATCH (p:Parameter {id: row.parameter_id})
MERGE (w)-[:HAS_PARAMETER]->(p)
"""

# --- Improvement-v2 §4 — :TableauParameterScope ---------------------------

MERGE_PARAMETER_SCOPE = """
UNWIND $rows AS row
MERGE (s:TableauParameterScope {id: row.id})
SET s.name = row.name,
    s.workbook_id = row.workbook_id,
    s.line = row.line
"""

HAS_PARAMETER_SCOPE = """
UNWIND $rows AS row
MATCH (w:TableauWorkbook {id: row.workbook_id})
MATCH (s:TableauParameterScope {id: row.scope_id})
MERGE (w)-[:HAS_PARAMETER_SCOPE]->(s)
"""

SCOPE_HAS_PARAMETER = """
UNWIND $rows AS row
MATCH (s:TableauParameterScope {id: row.scope_id})
MATCH (p:Parameter {id: row.parameter_id})
MERGE (s)-[:HAS_PARAMETER]->(p)
"""

# --- Improvement-v2 §6 — DERIVES_FROM_CROSS_DS ---------------------------

# Targets the calc :Attribute and points at the foreign :Attribute (or
# :Parameter). Two MERGE templates because the source may be either label;
# the writer dispatches by checking the symbol table at IR build time.

DERIVES_FROM_CROSS_DS_TO_ATTR = """
UNWIND $rows AS row
MATCH (tgt:Attribute {id: row.target_field_id})
MATCH (src:Attribute {id: row.source_field_id})
MERGE (tgt)-[r:DERIVES_FROM_CROSS_DS {id: row.id}]->(src)
SET r.source_datasource_name = row.source_datasource_name,
    r.char_start = row.char_start,
    r.char_end = row.char_end,
    r.formula_snippet = row.formula_snippet
"""

DERIVES_FROM_CROSS_DS_TO_PARAM = """
UNWIND $rows AS row
MATCH (tgt:Attribute {id: row.target_field_id})
MATCH (src:Parameter {id: row.source_field_id})
MERGE (tgt)-[r:DERIVES_FROM_CROSS_DS {id: row.id}]->(src)
SET r.source_datasource_name = row.source_datasource_name,
    r.char_start = row.char_start,
    r.char_end = row.char_end,
    r.formula_snippet = row.formula_snippet
"""

# --- Improvement-v2 §9 — worksheet data blending --------------------------

MERGE_WORKSHEET_BLEND = """
UNWIND $rows AS row
MERGE (b:WorksheetBlend {id: row.id})
SET b.worksheet_id = row.worksheet_id,
    b.primary_datasource_name = row.primary_datasource_name,
    b.secondary_datasource_name = row.secondary_datasource_name,
    b.on_field_names = row.on_field_names,
    b.line = row.line
"""

HAS_BLEND = """
UNWIND $rows AS row
MATCH (w:TableauWorksheet {id: row.worksheet_id})
MATCH (b:WorksheetBlend {id: row.id})
MERGE (w)-[:HAS_BLEND]->(b)
"""

BLENDS_WITH = """
UNWIND $rows AS row
MATCH (b:WorksheetBlend {id: row.blend_id})
MATCH (d:TableauDatasource {id: row.datasource_id})
MERGE (b)-[r:BLENDS_WITH {role: row.role}]->(d)
"""

# --- Overwrite support ----------------------------------------------------------

DELETE_WORKBOOK_SUBGRAPH = """
MATCH (w:TableauWorkbook {id: $workbook_id})
OPTIONAL MATCH (w)-[:CONTAINS_DATASOURCE]->(d)
OPTIONAL MATCH (w)-[:CONTAINS_WORKSHEET]->(s)
OPTIONAL MATCH (w)-[:CONTAINS_DASHBOARD]->(dash)
OPTIONAL MATCH (w)-[:HAS_PARAMETER]->(p)
OPTIONAL MATCH (w)-[:HAS_PARAMETER_SCOPE]->(ps)
OPTIONAL MATCH (s)-[:HAS_BLEND]->(bl:WorksheetBlend)
OPTIONAL MATCH (d)-[:HAS_FIELD]->(f:Attribute)
WHERE NOT (f)<-[:HAS_COLUMN]-(:Table)
DETACH DELETE w, d, s, dash, p, ps, bl, f
"""
