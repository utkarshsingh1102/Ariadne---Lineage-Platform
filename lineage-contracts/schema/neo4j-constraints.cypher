// =============================================================================
// lineage-contracts :: neo4j-constraints.cypher
// =============================================================================
// Idempotent constraints + indexes for the multi-parser knowledge graph.
// Applied on first boot of Neo4j (mounted into infra/neo4j/init.cypher).
// Safe to re-run: every statement uses IF NOT EXISTS.
//
// Grouped by source parser. Shared labels (:Table, :Attribute, :Script,
// :Connection) appear once and are MERGEd by all parsers.
// =============================================================================


// -----------------------------------------------------------------------------
// SHARED LABELS — written by multiple parsers, must share ID rules
// -----------------------------------------------------------------------------

CREATE CONSTRAINT table_fqn IF NOT EXISTS
  FOR (t:Table) REQUIRE t.fully_qualified_name IS UNIQUE;

CREATE CONSTRAINT attribute_id IF NOT EXISTS
  FOR (a:Attribute) REQUIRE a.id IS UNIQUE;

CREATE CONSTRAINT connection_id IF NOT EXISTS
  FOR (c:Connection) REQUIRE c.id IS UNIQUE;

CREATE CONSTRAINT script_path IF NOT EXISTS
  FOR (s:Script) REQUIRE s.path IS UNIQUE;

CREATE INDEX attribute_name IF NOT EXISTS
  FOR (a:Attribute) ON (a.name);

CREATE INDEX table_name IF NOT EXISTS
  FOR (t:Table) ON (t.name);


// -----------------------------------------------------------------------------
// TABLEAU parser
// -----------------------------------------------------------------------------

CREATE CONSTRAINT tableau_workbook_id IF NOT EXISTS
  FOR (w:TableauWorkbook) REQUIRE w.id IS UNIQUE;

CREATE CONSTRAINT tableau_datasource_id IF NOT EXISTS
  FOR (d:TableauDatasource) REQUIRE d.id IS UNIQUE;

CREATE CONSTRAINT tableau_worksheet_id IF NOT EXISTS
  FOR (w:TableauWorksheet) REQUIRE w.id IS UNIQUE;

CREATE CONSTRAINT tableau_dashboard_id IF NOT EXISTS
  FOR (d:TableauDashboard) REQUIRE d.id IS UNIQUE;

CREATE CONSTRAINT parameter_id IF NOT EXISTS
  FOR (p:Parameter) REQUIRE p.id IS UNIQUE;


// -----------------------------------------------------------------------------
// TWS parser  (added in Phase 2)
// -----------------------------------------------------------------------------

CREATE CONSTRAINT schedule_id IF NOT EXISTS
  FOR (s:Schedule) REQUIRE s.id IS UNIQUE;

CREATE CONSTRAINT job_id IF NOT EXISTS
  FOR (j:Job) REQUIRE j.id IS UNIQUE;

CREATE CONSTRAINT resource_id IF NOT EXISTS
  FOR (r:Resource) REQUIRE r.id IS UNIQUE;

CREATE CONSTRAINT file_watcher_id IF NOT EXISTS
  FOR (f:FileWatcher) REQUIRE f.id IS UNIQUE;

CREATE INDEX schedule_name IF NOT EXISTS
  FOR (s:Schedule) ON (s.name);

CREATE INDEX job_name IF NOT EXISTS
  FOR (j:Job) ON (j.name);

CREATE INDEX schedule_start_time IF NOT EXISTS
  FOR (s:Schedule) ON (s.start_time);


// -----------------------------------------------------------------------------
// QLIKVIEW parser  (added in Phase 3)
// -----------------------------------------------------------------------------

CREATE CONSTRAINT qlik_script_id IF NOT EXISTS
  FOR (s:QlikScript) REQUIRE s.id IS UNIQUE;

CREATE CONSTRAINT qlik_table_id IF NOT EXISTS
  FOR (t:QlikTable) REQUIRE t.id IS UNIQUE;

CREATE CONSTRAINT qlik_variable_id IF NOT EXISTS
  FOR (v:Variable) REQUIRE v.id IS UNIQUE;

CREATE CONSTRAINT qlik_subroutine_id IF NOT EXISTS
  FOR (s:Subroutine) REQUIRE s.id IS UNIQUE;

CREATE CONSTRAINT qlik_sheet_id IF NOT EXISTS
  FOR (s:QlikSheet) REQUIRE s.id IS UNIQUE;

CREATE INDEX qlik_chart_name IF NOT EXISTS
  FOR (c:QlikChart) ON (c.name);
