// MERGE_JOB_STREAM — 3 row(s)
// example row: {"id": "c580a628f6613a0b", "name": "NIGHTLY_INFRA_CHECK"}
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

// MERGE_SCHEDULE — 3 row(s)
// example row: {"id": "7b2e99e64b099785", "name": "NIGHTLY_INFRA_CHECK"}
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
    s.deadline = row.deadline,
    s.on_until = row.on_until,
    s.every = row.every,
    s.limit = row.limit,
    s.run_cycles = row.run_cycles,
    s.days_of_week = row.days_of_week,
    s.days_of_month = row.days_of_month,
    s.frequency = row.frequency,
    s.priority = row.priority,
    s.carry_forward = row.carry_forward,
    s.source_system = 'tws',
    s.source_files = [x IN coalesce(s.source_files, []) WHERE NOT x IN row.source_files] + row.source_files

// MERGE_JOB — 10 row(s)
// example row: {"id": "8d9ffc4c8c264e40", "name": "CHECK_DISK"}
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

// CONTAINS_JOB — 10 row(s)
// example row: {"schedule_id": "7b2e99e64b099785", "job_id": "8d9ffc4c8c264e40", "order": 0}
UNWIND $rows AS row
MATCH (s:Schedule {id: row.schedule_id})
MATCH (j:Job {id: row.job_id})
MERGE (s)-[r:CONTAINS_JOB]->(j)
SET r.order = row.order

// DEPENDS_ON_JOB — 7 row(s)
// example row: {"from_id": "4c0acb03fcf872a2", "to_id": "8d9ffc4c8c264e40", "condition": "", "scope": "internal"}
UNWIND $rows AS row
MATCH (a:Job {id: row.from_id})
MATCH (b:Job {id: row.to_id})
MERGE (a)-[r:DEPENDS_ON {condition: row.condition}]->(b)
SET r.dependency_type = 'follows',
    r.scope = row.scope
