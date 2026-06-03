"""Cypher MERGE templates.

v0.2 conventions (per the user-approved hybrid naming decision):

* DEPENDS_ON stays as-is for job→job and schedule→schedule dependencies
  (heavily used across gateway presets + frontend). Job→job gains a
  ``condition`` property that is PART OF the MERGE key so two predecessors
  with different conditions (``RC=0`` vs ``RC=4``) produce TWO distinct edges.
* CALLS_SCRIPT → EXECUTES (cross-parser convention).
* NEEDS_RESOURCE → REQUIRES_RESOURCE.
* Five new node labels: :Workstation, :JobStream, :Calendar, :Prompt, :EventRule.
* Five new edges: :RUNS_ON, :WAITS_FOR_PROMPT, :RECOVERS_WITH, :TRIGGERS,
  :SCHEDULED_BY.

Multi-file provenance — every node MERGE accepts ``row.source_files`` (a
list of file paths) and dedups against any pre-existing ``source_files``
property via a pure-Cypher list-comprehension expression (no APOC). The
shape is:

  n.source_files = [x IN coalesce(n.source_files, []) WHERE NOT x IN row.source_files]
                 + row.source_files

This keeps the property deduped+ordered across repeated MERGEs from the
same file (idempotent) AND across distinct multi-file uploads.
"""

PARSER_NAME = "tws-parser"


# ---------------------------------------------------------------------------
# Node MERGEs
# ---------------------------------------------------------------------------

MERGE_SCHEDULE = """
UNWIND $rows AS row
MERGE (s:Schedule {id: row.id})
SET s.name = row.name,
    s.workstation = row.workstation,
    s.scheduler = row.scheduler,
    s.run_cycle = row.run_cycle,
    s.cron_equivalent = row.cron_equivalent,
    s.valid_from = row.valid_from,
    s.valid_to = row.valid_to,
    s.start_time = row.start_time,
    s.end_time = row.end_time,
    s.priority = row.priority,
    s.carry_forward = row.carry_forward,
    s.source_system = 'tws',
    s.source_files = [x IN coalesce(s.source_files, []) WHERE NOT x IN row.source_files] + row.source_files
"""

MERGE_JOB = """
UNWIND $rows AS row
MERGE (j:Job {id: row.id})
SET j.name = row.name,
    j.qualified_name = row.qualified_name,
    j.schedule_id = row.schedule_id,
    j.workstation = row.workstation,
    j.stream = row.stream,
    j.stream_logon = row.stream_logon,
    j.recovery = row.recovery,
    j.description = row.description,
    j.priority = row.priority,
    j.order_in_schedule = row.order_in_schedule,
    j.source_system = 'tws',
    j.source_files = [x IN coalesce(j.source_files, []) WHERE NOT x IN row.source_files] + row.source_files
"""

MERGE_WORKSTATION = """
UNWIND $rows AS row
MERGE (w:Workstation {id: row.id})
SET w.name = row.name,
    w.description = row.description,
    w.os = row.os,
    w.node = row.node,
    w.tcp_addr = row.tcp_addr,
    w.type = row.type,
    w.domain = row.domain,
    w.autolink = row.autolink,
    w.behind_firewall = row.behind_firewall,
    w.source_system = 'tws',
    w.source_files = [x IN coalesce(w.source_files, []) WHERE NOT x IN row.source_files] + row.source_files
"""

MERGE_JOB_STREAM = """
UNWIND $rows AS row
MERGE (js:JobStream {id: row.id})
SET js.name = row.name,
    js.qualified_name = row.qualified_name,
    js.workstation = row.workstation,
    js.description = row.description,
    js.start_time = row.start_time,
    js.end_time = row.end_time,
    js.deadline = row.deadline,
    js.priority = row.priority,
    js.limit = row.limit,
    js.carry_forward = row.carry_forward,
    js.valid_from = row.valid_from,
    js.valid_to = row.valid_to,
    js.run_cycles = row.run_cycles,
    js.every = row.every,
    js.on_until = row.on_until,
    js.source_system = 'tws',
    js.source_files = [x IN coalesce(js.source_files, []) WHERE NOT x IN row.source_files] + row.source_files
"""

MERGE_CALENDAR = """
UNWIND $rows AS row
MERGE (c:Calendar {id: row.id})
SET c.name = row.name,
    c.description = row.description,
    c.dates = row.dates,
    c.source_system = 'tws',
    c.source_files = [x IN coalesce(c.source_files, []) WHERE NOT x IN row.source_files] + row.source_files
"""

MERGE_PROMPT = """
UNWIND $rows AS row
MERGE (p:Prompt {id: row.id})
SET p.name = row.name,
    p.text = row.text,
    p.source_system = 'tws',
    p.source_files = [x IN coalesce(p.source_files, []) WHERE NOT x IN row.source_files] + row.source_files
"""

MERGE_EVENT_RULE = """
UNWIND $rows AS row
MERGE (er:EventRule {id: row.id})
SET er.name = row.name,
    er.description = row.description,
    er.active = row.active,
    er.rule_type = row.rule_type,
    er.event_type = row.event_type,
    er.event_node = row.event_node,
    er.event_filename = row.event_filename,
    er.action_type = row.action_type,
    er.target_stream_qualified = row.target_stream_qualified,
    er.source_system = 'tws',
    er.source_files = [x IN coalesce(er.source_files, []) WHERE NOT x IN row.source_files] + row.source_files
"""

MERGE_SCRIPT = """
UNWIND $rows AS row
MERGE (s:Script {path: row.path})
ON CREATE SET s.id = row.id,
              s.script_type = row.script_type,
              s.first_seen_by = $parser
ON MATCH SET  s.script_type = coalesce(s.script_type, row.script_type)
SET s.source_files = [x IN coalesce(s.source_files, []) WHERE NOT x IN row.source_files] + row.source_files
"""

MERGE_RESOURCE = """
UNWIND $rows AS row
MERGE (r:Resource {id: row.id})
SET r.name = row.name,
    r.quantity = row.quantity,
    r.source_files = [x IN coalesce(r.source_files, []) WHERE NOT x IN row.source_files] + row.source_files
"""

MERGE_FILE_WATCHER = """
UNWIND $rows AS row
MERGE (f:FileWatcher {id: row.id})
SET f.path = row.path,
    f.pattern = row.pattern,
    f.source_files = [x IN coalesce(f.source_files, []) WHERE NOT x IN row.source_files] + row.source_files
"""


# ---------------------------------------------------------------------------
# Edge MERGEs
# ---------------------------------------------------------------------------

CONTAINS_JOB = """
UNWIND $rows AS row
MATCH (s:Schedule {id: row.schedule_id})
MATCH (j:Job {id: row.job_id})
MERGE (s)-[r:CONTAINS_JOB]->(j)
SET r.order = row.order
"""

# v0.2 — JobStream also CONTAINS_JOB. The presets walk this label too.
CONTAINS_JOB_VIA_STREAM = """
UNWIND $rows AS row
MATCH (js:JobStream {id: row.stream_id})
MATCH (j:Job {id: row.job_id})
MERGE (js)-[r:CONTAINS_JOB]->(j)
SET r.order = row.order
"""

# v0.2 RENAMED — was CALLS_SCRIPT. Cross-parser convention.
EXECUTES = """
UNWIND $rows AS row
MATCH (j:Job {id: row.job_id})
MATCH (s:Script {path: row.path})
MERGE (j)-[r:EXECUTES]->(s)
SET r.args = row.args
"""

# v0.2 — condition is part of the MERGE key so two predecessors with
# different conditions (RC=0 vs RC=4) produce TWO distinct edges.
DEPENDS_ON_JOB = """
UNWIND $rows AS row
MATCH (a:Job {id: row.from_id})
MATCH (b:Job {id: row.to_id})
MERGE (a)-[r:DEPENDS_ON {condition: row.condition}]->(b)
SET r.dependency_type = 'follows',
    r.scope = row.scope
"""

DEPENDS_ON_SCHEDULE = """
UNWIND $rows AS row
MATCH (a:Schedule {id: row.from_id})
MATCH (b:Schedule {id: row.to_id})
MERGE (a)-[r:DEPENDS_ON]->(b)
SET r.dependency_type = 'follows'
"""

# v0.2 RENAMED — was NEEDS_RESOURCE.
REQUIRES_RESOURCE = """
UNWIND $rows AS row
MATCH (j:Job {id: row.job_id})
MATCH (r:Resource {id: row.resource_id})
MERGE (j)-[rel:REQUIRES_RESOURCE]->(r)
SET rel.quantity = row.quantity
"""

WAITS_FOR_FILE = """
UNWIND $rows AS row
MATCH (j:Job {id: row.job_id})
MATCH (f:FileWatcher {id: row.file_watcher_id})
MERGE (j)-[:WAITS_FOR_FILE]->(f)
"""

# v0.2 NEW edges -----------------------------------------------------------

RUNS_ON = """
UNWIND $rows AS row
MATCH (j:Job {id: row.job_id})
MATCH (w:Workstation {id: row.workstation_id})
MERGE (j)-[:RUNS_ON]->(w)
"""

WAITS_FOR_PROMPT = """
UNWIND $rows AS row
MATCH (j:Job {id: row.job_id})
MATCH (p:Prompt {id: row.prompt_id})
MERGE (j)-[:WAITS_FOR_PROMPT]->(p)
"""

RECOVERS_WITH = """
UNWIND $rows AS row
MATCH (a:Job {id: row.from_id})
MATCH (b:Job {id: row.to_id})
MERGE (a)-[r:RECOVERS_WITH]->(b)
SET r.recovery_action = row.recovery_action
"""

TRIGGERS = """
UNWIND $rows AS row
MATCH (er:EventRule {id: row.event_rule_id})
MATCH (js:JobStream {id: row.job_stream_id})
MERGE (er)-[:TRIGGERS]->(js)
"""

SCHEDULED_BY = """
UNWIND $rows AS row
MATCH (js:JobStream {id: row.stream_id})
MATCH (c:Calendar {id: row.calendar_id})
MERGE (js)-[:SCHEDULED_BY]->(c)
"""

# Workstation hosts JobStream — fixed topology shape ("this stream runs on
# this workstation"). Mirrors the per-job RUNS_ON edge at stream granularity.
HOSTS_STREAM = """
UNWIND $rows AS row
MATCH (w:Workstation {id: row.workstation_id})
MATCH (js:JobStream {id: row.stream_id})
MERGE (w)-[:HOSTS_STREAM]->(js)
"""

DELETE_SCHEDULE_SUBGRAPH = """
MATCH (s:Schedule {id: $schedule_id})
OPTIONAL MATCH (s)-[:CONTAINS_JOB]->(j:Job)
DETACH DELETE s, j
"""

# Subgraph delete keyed on the JobStream id — used when overwriting in the
# v0.2 path so JobStream + downstream nodes get cleaned up too.
DELETE_JOB_STREAM_SUBGRAPH = """
MATCH (js:JobStream {id: $stream_id})
OPTIONAL MATCH (js)-[:CONTAINS_JOB]->(j:Job)
DETACH DELETE js, j
"""
