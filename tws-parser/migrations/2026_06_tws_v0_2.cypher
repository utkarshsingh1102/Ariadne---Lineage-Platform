// TWS Parser v0.2 — one-shot migration
//
// Run this ONCE against the target Neo4j database BEFORE deploying the v0.2
// parser image. It drops the v0.1 artifacts that the new parser will
// re-emit under different ids / relationship types.
//
// What changes between v0.1 and v0.2 that requires this:
//
//   1. ``job_id`` derivation switched to ``make_id("job", workstation,
//      stream, name)`` — the cross-parser qualified-string convention. Old
//      :Job nodes hashed under the legacy ``(schedule_id, name)`` scheme
//      hold IDs the new parser will never re-MERGE on. They become orphans
//      unless deleted.
//
//   2. The :CALLS_SCRIPT relationship type was renamed to :EXECUTES, and
//      :NEEDS_RESOURCE to :REQUIRES_RESOURCE, to match the cross-parser
//      naming used by the Tableau / Spark / QlikView writers. Old edges
//      would still be in the graph; the new edges land alongside them
//      unless we delete the old shapes here.
//
//   3. Topology nodes (:Workstation, :JobStream, :Calendar, :Prompt,
//      :EventRule) didn't exist in v0.1 — no cleanup needed for those.
//
// Re-running the parser after this migration repopulates the graph under
// the new shape via the writer's MERGE statements. No data is lost — the
// parser re-derives everything from the source files.
//
// To run:
//   docker exec lineage-neo4j cypher-shell -u neo4j -p <password> \
//     -f /migrations/2026_06_tws_v0_2.cypher

// --- Drop renamed edges --------------------------------------------------
MATCH ()-[r:CALLS_SCRIPT]->() DELETE r;
MATCH ()-[r:NEEDS_RESOURCE]->() DELETE r;

// --- Drop old :Job and :Schedule nodes (id scheme changed) ---------------
// :Job ids changed to the qualified-string hash; old nodes can't be MERGEd
// against. :Schedule ids didn't change but we wipe them too so any orphaned
// CONTAINS_JOB edges go with them. The parser re-emits both on the next
// /parse call.
MATCH (j:Job) DETACH DELETE j;
MATCH (s:Schedule) DETACH DELETE s;

// --- Ensure v0.2 constraints exist (idempotent) --------------------------
// The writer also calls ensure_constraints() at startup, but declaring
// them here lets operators run the migration before the new image ships.
CREATE CONSTRAINT schedule_id     IF NOT EXISTS FOR (s:Schedule)    REQUIRE s.id IS UNIQUE;
CREATE CONSTRAINT job_id          IF NOT EXISTS FOR (j:Job)         REQUIRE j.id IS UNIQUE;
CREATE CONSTRAINT script_path     IF NOT EXISTS FOR (s:Script)      REQUIRE s.path IS UNIQUE;
CREATE CONSTRAINT resource_id     IF NOT EXISTS FOR (r:Resource)    REQUIRE r.id IS UNIQUE;
CREATE CONSTRAINT file_watcher_id IF NOT EXISTS FOR (f:FileWatcher) REQUIRE f.id IS UNIQUE;
CREATE CONSTRAINT workstation_id  IF NOT EXISTS FOR (w:Workstation) REQUIRE w.id IS UNIQUE;
CREATE CONSTRAINT job_stream_id   IF NOT EXISTS FOR (js:JobStream)  REQUIRE js.id IS UNIQUE;
CREATE CONSTRAINT calendar_id     IF NOT EXISTS FOR (c:Calendar)    REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT prompt_id       IF NOT EXISTS FOR (p:Prompt)      REQUIRE p.id IS UNIQUE;
CREATE CONSTRAINT event_rule_id   IF NOT EXISTS FOR (er:EventRule)  REQUIRE er.id IS UNIQUE;
